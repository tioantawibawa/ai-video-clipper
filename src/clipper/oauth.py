"""Small owner-operated TikTok Web OAuth service, behind an HTTPS reverse proxy.

Run with gunicorn; issue connect tickets from SSH with ``python -m clipper.oauth``.
No public endpoint can create tickets or read credentials/tokens.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

CALLBACK = "/oauth/tiktok/callback"


class OAuth:
    def __init__(self, root, origin, key="", secret=""):
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("OAuth origin must be an HTTPS origin without path")
        self.origin, self.key, self.secret = origin, key, secret
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root / "oauth.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS grants (id TEXT PRIMARY KEY, account TEXT, kind TEXT, expires REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS tokens (account TEXT PRIMARY KEY, payload TEXT)")
        os.chmod(self.db, 0o600)

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    def issue(self, account, kind="ticket"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", account):
            raise ValueError("Invalid account id")
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM grants WHERE expires < ?", (time.time(),))
            db.execute("INSERT INTO grants VALUES (?,?,?,?)", (self.digest(token), account, kind, time.time() + 600))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def consume(self, token, kind):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT account FROM grants WHERE id=? AND kind=? AND expires>?",
                             (self.digest(token), kind, time.time())).fetchone()
            db.execute("DELETE FROM grants WHERE id=?", (self.digest(token),))
        return row[0] if row else None

    def exchange(self, **fields):
        data = urlencode(dict(client_key=self.key, client_secret=self.secret, **fields)).encode()
        request = Request("https://open.tiktokapis.com/v2/oauth/token/", data=data,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read(65536))
        if result.get("error") or not all(result.get(k) for k in ("access_token", "refresh_token", "open_id")):
            raise ValueError("Token exchange failed")
        result["expires_at"] = time.time() + int(result["expires_in"])
        return result

    def store(self, account, result):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO tokens VALUES (?,?)", (account, json.dumps(result)))

    def refresh(self, account):
        # Serializes refresh and callback writes; rotating refresh tokens must not race.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM tokens WHERE account=?", (account,)).fetchone()
            if not row:
                raise ValueError("Account has not connected")
            old = json.loads(row[0])
            result = self.exchange(grant_type="refresh_token", refresh_token=old["refresh_token"])
            if result["open_id"] != old["open_id"]:
                raise ValueError("Unexpected account identity")
            db.execute("UPDATE tokens SET payload=? WHERE account=?", (json.dumps(result), account))

    def __call__(self, env, start):
        headers = [("Content-Type", "text/plain; charset=utf-8"), ("Cache-Control", "no-store"),
                   ("Referrer-Policy", "no-referrer"), ("X-Content-Type-Options", "nosniff"),
                   ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")]

        def reply(code, text, extra=()):
            start(code, headers + list(extra))
            return [text.encode()]

        if env.get("REQUEST_METHOD") != "GET":
            return reply("405 Method Not Allowed", "GET required")
        path = env.get("PATH_INFO", "")
        if path == "/healthz":
            return reply("200 OK", "OAuth service running")
        if path not in ("/connect", CALLBACK):
            return reply("404 Not Found", "Not found")
        if not self.key or not self.secret:
            return reply("503 Service Unavailable", "TikTok credentials are not configured yet.")
        try:
            query = parse_qs(env.get("QUERY_STRING", ""), max_num_fields=10)
            if any(len(v) != 1 for v in query.values()):
                return reply("400 Bad Request", "Invalid request")
            if path == "/connect":
                account = self.consume(query.get("ticket", [""])[0], "ticket")
                if not account:
                    return reply("403 Forbidden", "Invalid or expired connection link. Generate a new link over SSH.")
                state = self.issue(account, "state")
                url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(dict(
                    client_key=self.key, response_type="code", scope="user.info.basic,video.upload",
                    redirect_uri=self.origin + CALLBACK, state=state, disable_auto_auth=1))
                return reply("302 Found", "Continue to TikTok", [("Location", url),
                    ("Set-Cookie", f"__Host-clipper_state={state}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600")])
            cookie = SimpleCookie(env.get("HTTP_COOKIE", ""))
            state = query.get("state", [""])[0]
            bound = cookie.get("__Host-clipper_state")
            if not state or not bound or not secrets.compare_digest(state, bound.value):
                return reply("403 Forbidden", "Invalid OAuth state. Start from a new connection link.")
            account = self.consume(state, "state")
            if not account:
                return reply("403 Forbidden", "Expired or already used OAuth state")
            if query.get("error") or not query.get("code"):
                return reply("400 Bad Request", "Authorization was declined or incomplete. Start again when ready.")
            result = self.exchange(grant_type="authorization_code", code=query["code"][0],
                                   redirect_uri=self.origin + CALLBACK)
            if not {"user.info.basic", "video.upload"}.issubset(set(result.get("scope", "").split(","))):
                return reply("400 Bad Request", "Required permissions were not granted. Reconnect with the required scopes.")
            self.store(account, result)
            return reply("200 OK", "TikTok connected. Credentials saved privately on the server. You may close this page.",
                         [("Set-Cookie", "__Host-clipper_state=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0")])
        except Exception:
            # Never expose authorization codes, token payloads or provider exception URLs.
            return reply("502 Bad Gateway", "Authorization could not complete. Generate a new connection link and retry.")


def create_app():
    return OAuth(os.environ.get("CLIPPER_OAUTH_DATA", "data/oauth"), os.environ["CLIPPER_OAUTH_ORIGIN"],
                 os.environ.get("CLIPPER_TIKTOK_CLIENT_KEY", ""), os.environ.get("CLIPPER_TIKTOK_CLIENT_SECRET", ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["connect", "refresh"])
    parser.add_argument("account")
    args = parser.parse_args()
    service = create_app()
    if args.action == "connect":
        print(service.origin + "/connect?" + urlencode({"ticket": service.issue(args.account)}))
    else:
        service.refresh(args.account)
        print("Token refreshed; no credentials displayed.")
