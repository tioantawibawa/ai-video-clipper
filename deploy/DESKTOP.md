# Run entirely on the laptop

The desktop workflow downloads, transcribes, selects moments, renders and uploads
directly from Windows. It does not use SSH, SCP, a VPS, or a remote queue.
Research recommendations remain in the daily 12:00 WIB conversation automation.
Research links remain recommendations. Finished candidates explicitly submitted
to the local queue are automatically uploaded Private. Historical staging folders
are never scanned or replayed.

## Reuse the existing D: uploader

Set `upload_backend` to `autopilot`, `autopilot_root` to `D:\\youtube short`, and
`auto_upload_candidates` to `true` in `data/desktop/config.json`. The bridge runs
that project's `venv/Scripts/python.exe`, imports `scripts/step4_uploader.py`, and
calls its actual `upload_short` with `privacy_status="private"`. OAuth stays in
the existing `config/token.json`; credentials are not copied into this repository.
No synthetic video, voiceover, research or Telegram pipeline from D: is executed.

```powershell
.\scripts\desktop.ps1 doctor --live
.\scripts\desktop.ps1 submit 'data\my-clip\clip.mp4' 'data\my-clip\manifest.json'
.\scripts\desktop.ps1 watch --once
.\scripts\install-candidate-uploader.ps1
```

The Windows task `Clipper Candidate Private Uploader` starts at login and at 12:00,
checks ready tickets every minute, and ignores duplicate starts. Logs are stored
in `data/desktop/candidate-worker.log`. The laptop must be logged in, awake and
online. Stop it with `Stop-ScheduledTask -TaskName 'Clipper Candidate Private Uploader'`.
It does not change the separate D: project's schedules. Both desktop and standard
CLI `process` submit newly rendered candidates when automatic mode is enabled.
Custom/manual edits must use `submit` after checking the final file and captions.
Set `uploads_enabled` to `false` and install with `-Paused` to hold all uploads
when reconciling a channel mismatch. Confirm the intended channel, update
`expected_channel`, enable uploads, and rerun the installer to resume.

The content hash and SQLite reservation prevent duplicate uploads. A known video
ID is saved before status checks and dashboard registration. An interrupted
upload without an ID is held for manual reconciliation, never retried automatically.
Failed tickets remain `needs_attention`; inspect the ledger and Studio before
changing their state. Successful uploads are also registered best-effort in the
existing D: DuckDB dashboard; dashboard failure never triggers another upload.

An upload-only OAuth token may not permit reading channel/video status. In that
case the returned insert response verifies channel and Private privacy, while
processing is explicitly recorded as `unverified`. Do not report it as processed.
Reauthorize through the original project's helper if refresh authorization expires.
The native OAuth instructions below apply only to the `native` backend.

## Configuration

Copy `desktop.example.json` to `data/desktop/config.json`. Create
`data/desktop/sources.json` containing `[]`, and `data/desktop/accounts.json`:

```json
[{"id":"podcast-us-youtube","platform":"youtube","token_env":"YOUTUBE_ACCESS_TOKEN",
  "client_id_env":"YOUTUBE_CLIENT_ID","client_secret_env":"YOUTUBE_CLIENT_SECRET",
  "refresh_token_env":"YOUTUBE_REFRESH_TOKEN","privacy":"private","timezone":"Asia/Jakarta"}]
```

Set `expected_channel` in the desktop config to the intended channel ID.
LLM settings are read from `credentials/local-worker.env`. OAuth credentials
are saved separately in `credentials/desktop/youtube.env`; neither file is tracked.
Restrict the `credentials/desktop` directory ACL to your Windows account and SYSTEM
before authorizing. Atomic credential replacements then inherit this restricted ACL.
The desktop explicitly overrides any former `/data` environment paths. Processing
uses multilingual Whisper `small`, preserves source language, and stages for review.

## Connect YouTube locally

Enable YouTube Data API v3 in your Google Cloud project. Configure the consent
screen, add yourself as a test user if applicable, and create an OAuth client of
type **Desktop app**. Download its JSON into a private folder.

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe -m clipper.youtube_auth --client-file 'C:\private\client_secret.json'
# Or use the Windows wrapper:
.\scripts\connect-youtube.ps1 -ClientFile 'C:\private\client_secret.json'
```

The default browser opens Google consent. Select the channel owner and complete
the permissions yourself. A local HTTP callback listens only on `127.0.0.1`,
uses a random port, state validation and PKCE, then closes. Tokens are never
printed. The channel must match `expected_channel` before anything is saved.
No VPS callback, domain, inbound internet firewall rule, or OAuth Playground is
needed. External apps in Testing may receive refresh tokens expiring after seven
days; follow Google's production/verification requirements for your application.

## Commands from this project

```powershell
.\scripts\desktop.ps1 doctor
.\scripts\desktop.ps1 doctor --live
.\scripts\desktop.ps1 process 'https://www.youtube.com/watch?v=VIDEO_ID'
.\scripts\desktop.ps1 upload 'data\my-clip\clip.mp4' 'data\my-clip\manifest.json'
.\scripts\desktop.ps1 queue
```

With automatic mode disabled, `process` creates reviewable clips only. For an already edited clip, use `upload`
with its manifest (`metadata.title`, `description`, `hashtags`, optional
`clip_sha256`). Every upload is private and has no scheduled public release.
The channel and privacy are checked before an accepted receipt is written to
`data/desktop/receipts/`. Processing is verified separately when read access exists.

When `data/desktop/config.json` exists, the standard `python -m clipper` CLI also
uses desktop settings, including `process`, `queue`, `review` and `daemon`. The
desktop `upload` command remains the immediate private-upload entrypoint;
the autopilot backend's daemon consumes submitted candidate tickets.
The former Windows VPS worker launchers refuse to start while desktop mode is active.

The local SQLite queue deduplicates by account and SHA-256, including files copied
to another directory. Authentication is checked before creating an upload job.
An interrupted upload without a known ID remains `uncertain`; do not retry it under
another path. Check YouTube Studio and reconcile the durable job first. Known IDs
are polled without re-uploading. Successful private uploads stay `held` and are not
eligible for the public scheduler. A processing timeout retains the ID for polling.

## Cut over from the former VPS

Disable and stop only the old clipper publishing/manager services and the Windows
`Clipper Daily Podcast Preparation` task. Preserve all media, credentials and
queue data. Take a consistent queue snapshot while the services are stopped.
Store a sanitized job history with hashes in `data/desktop/migration-history.json`;
do not replay old jobs, old schedules, or ambiguous upload outcomes. The local uploader
refuses any clip already found in that history until it has been reconciled.
The former queue can be kept as a backup; it is not a runtime dependency.

The laptop must be awake and connected during rendering/upload. No background
worker continues after Windows shuts down. Running the same command again is safe
only when its stored outcome is known; uncertain outcomes require reconciliation.
