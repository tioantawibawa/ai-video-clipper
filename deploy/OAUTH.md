# TikTok Web OAuth on Ubuntu

This separate, lightweight service supports owner-initiated Web OAuth and a
manual refresh command. It does not implement the creator export UI or automatically
refresh publisher credentials. The draft adapter accepts `video.upload` tokens via
the account's token environment variable; stored OAuth tokens are not automatically
exported into that environment. Never treat the health check as a completed TikTok
integration test. Keep publishing disabled until that integration is completed.

Install Python venv support, create a dedicated `clipper-oauth` system user, copy
`src/clipper/__init__.py` and `src/clipper/oauth.py` to `/opt/clipper-oauth/src/clipper`,
create `/opt/clipper-oauth/venv`, and install `deploy/oauth-requirements.txt` there.
Install the provided systemd service. Keep code owned by root, and service data
in `/var/lib/clipper-oauth` owned by the service account with mode 0700.

Create `/etc/clipper-oauth.env` (root owned, mode 0600):

```ini
CLIPPER_OAUTH_ORIGIN=https://YOUR-HOST
CLIPPER_TIKTOK_CLIENT_KEY=
CLIPPER_TIKTOK_CLIENT_SECRET=
```

Put the real key and secret in that file using a private SSH session, never git.
The service starts without keys but responds 503 to OAuth requests until configured.

Add a Caddy site using your hostname:

```caddy
YOUR-HOST {
    reverse_proxy 127.0.0.1:8787
}
```

Do not enable access logging for OAuth query strings. Allow inbound 80/443 in the
cloud firewall for ACME/HTTPS; keep 8787 bound only to loopback. Validate Caddy's
configuration before reloading. Back up existing configuration and preserve sites.
An IP-based sslip.io hostname can be used initially, but depends on the DNS provider
and retaining that IP; a domain you control is preferable for long-term use.

Choose Web in the TikTok portal and register exactly:
`https://YOUR-HOST/oauth/tiktok/callback`. Enable Login Kit and Content Posting API.
The flow requests `user.info.basic,video.upload`; it does not request Direct Post.

Start/restart `clipper-oauth` after configuring credentials. To generate a ten-minute,
one-use owner connection link, run this over SSH (the link itself is sensitive):

```sh
sudo systemd-run --quiet --wait --pipe --collect -p User=clipper-oauth \
  -p EnvironmentFile=/etc/clipper-oauth.env \
  -p Environment=PYTHONPATH=/opt/clipper-oauth/src \
  -p Environment=CLIPPER_OAUTH_DATA=/var/lib/clipper-oauth \
  /opt/clipper-oauth/venv/bin/python -m clipper.oauth connect main
```

Open it in your browser and authorize your own TikTok account. For Sandbox, use
the sandbox credentials and allowed test account. Tokens are stored in a private
SQLite database, never returned by an HTTP endpoint. Keep the disk and backups
protected. The browser is bound to the flow using a Secure, HttpOnly, SameSite
cookie and a single-use, expiring state. Replacing a connected account requires
a new SSH-generated link. Repeat the command with `refresh main` instead of
`connect main` for manual refresh (rotated refresh tokens are persisted).

Verify an actual consent and callback in Sandbox before submission for review.
Stop with `sudo systemctl stop clipper-oauth` if deployment needs rollback.
