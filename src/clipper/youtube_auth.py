"""Authorize YouTube on this computer using a Desktop OAuth client and loopback PKCE."""
import argparse
import asyncio
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

from dotenv import dotenv_values
import httpx

from .desktop import read_config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]


def save_env(path, values):
    """Preserve other settings; atomic replacement and no credential logging."""
    current = dict(dotenv_values(path)) if path.exists() else {}
    current.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text("\n".join(k + "=" + json.dumps(v) for k, v in current.items()
                                 if v is not None) + "\n", encoding="utf-8")
    pending.chmod(0o600)
    pending.replace(path)


def desktop_client(path):
    client = json.loads(path.read_text(encoding="utf-8-sig")).get("installed", {})
    if not client.get("client_id") or not client.get("client_secret"):
        raise ValueError("Download an OAuth client JSON of type Desktop app from Google Cloud")
    return client


async def authorize(cfg, client_file, timeout=300):
    client = desktop_client(client_file)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Authorization codes must never enter HTTP logs.

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path != "/callback" or query.get("state", [""])[0] != state:
                self.send_error(400, "Invalid authorization callback")
                return
            if query.get("error"):
                result["error"] = "Authorization declined"
            elif query.get("code"):
                result["code"] = query["code"][0]
            else:
                self.send_error(400, "Missing authorization code")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authorization received. Return to the Clipper terminal.")

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": client["client_id"], "redirect_uri": redirect,
            "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline",
            "prompt": "consent", "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256"})
        print("Opening Google consent. Sign in to the intended YouTube channel. No tokens will be printed.", flush=True)
        if not webbrowser.open(url):
            raise RuntimeError("Could not open the default browser")
        deadline = asyncio.get_running_loop().time() + timeout
        while not result and asyncio.get_running_loop().time() < deadline:
            await asyncio.to_thread(server.handle_request)
    if not result.get("code"):
        raise ValueError("Authorization was declined or timed out")
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "authorization_code", "code": result["code"],
            "client_id": client["client_id"], "client_secret": client["client_secret"],
            "redirect_uri": redirect, "code_verifier": verifier})
        response.raise_for_status()
        tokens = response.json()
        if not tokens.get("refresh_token"):
            raise ValueError("Google did not issue an offline refresh token; credentials unchanged")
        r = await http.get("https://www.googleapis.com/youtube/v3/channels",
                           params={"part": "snippet", "mine": "true"},
                           headers={"Authorization": "Bearer " + tokens["access_token"]})
        r.raise_for_status()
        channels = r.json().get("items", [])
        if len(channels) != 1 or (cfg.expected_channel and channels[0]["id"] != cfg.expected_channel):
            raise ValueError("Wrong YouTube channel; credentials unchanged")
    save_env(cfg.env_file, {"YOUTUBE_CLIENT_ID": client["client_id"],
                           "YOUTUBE_CLIENT_SECRET": client["client_secret"],
                           "YOUTUBE_REFRESH_TOKEN": tokens["refresh_token"]})
    print(json.dumps({"connected": True, "channel_id": channels[0]["id"],
                      "title": channels[0]["snippet"]["title"], "credentials": str(cfg.env_file)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("data/desktop/config.json"))
    parser.add_argument("--client-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(authorize(read_config(args.config), args.client_file))
    except Exception as exc:
        print("YouTube authorization failed (" + type(exc).__name__ + "). No tokens displayed.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
