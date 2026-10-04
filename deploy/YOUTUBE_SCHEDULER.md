# YouTube completed-clip scheduler

The VPS worker scans `/home/ubuntu/ai-video-clipper/data/youtube-outbox` every 30 seconds.
Place a completed MP4 and its JSON manifest there. Copy the MP4 first, then atomically
rename the completed JSON into place. The manifest uses `file`, `metadata.title`,
`metadata.description`, `metadata.hashtags`, and `published: false`.
Only put clips you intend to publish in this folder: review is bypassed for this inbox.

The account `podcast-us-youtube` publishes publicly, at most once per calendar day
in America/New_York. Slots are randomized using the existing durable SQLite queue.
Late jobs are rescheduled; uncertain upload outcomes require reconciliation.
Do not copy clips already uploaded in Studio unless their manifest has `published: true`
or `remote_id`, which causes the inbox scanner to skip them. Renaming a clip creates
a different queue identity; keep stable filenames to prevent reposts.

Configure the account's `refresh_token_env`, `client_id_env`, `client_secret_env`:
`YOUTUBE_REFRESH_TOKEN`, `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` in the VPS `.env`
(mode 600). Obtain offline OAuth consent with the YouTube upload scope. Tokens are
refreshed before uploads; missing or rejected credentials leave jobs queued.
An access token alone is insufficient for unattended publishing.

Commands on the VPS:

```sh
sudo systemctl restart clipper-vps.service
sudo systemctl status clipper-vps.service
journalctl -u clipper-vps.service -n 50 --no-pager
CLIPPER_DATA_DIR=data/vps CLIPPER_ACCOUNTS_FILE=data/vps/accounts.json .venv/bin/python -m clipper queue
```

Google OAuth reference: https://developers.google.com/identity/protocols/oauth2/web-server#offline
