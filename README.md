# AI Video Clipper & Publisher Agent

Python 3.11/3.12, headless Ubuntu, a `src/clipper/` package, SQLite, FFmpeg/libass,
and official publishing APIs. OpenRouter free routing and review are enabled by default. Source media should
be owned by you or licensed for clipping and redistribution.

## Quick start (Ubuntu 24.04)

```bash
sudo apt-get update
sudo apt-get install -y python3-venv ffmpeg fonts-dejavu-core libgl1 libglib2.0-0
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps -e .
cp .env.example .env
cp accounts.example.json accounts.json
cp sources.example.json sources.json
# Set CLIPPER_OPENROUTER_API_KEY in .env; remove unused accounts and replace the example source.
chmod 600 .env accounts.json
python -m clipper doctor
python -m clipper process 'https://www.youtube.com/watch?v=VIDEO_ID'
# Stage and enqueue for a configured account:
python -m clipper process 'https://www.youtube.com/watch?v=VIDEO_ID' --account podcast-us-youtube
python -m clipper queue
# Open data/staging/*.mp4 and the corresponding JSON before approving.
python -m clipper review 1 --approve
python -m clipper daemon
```

Processing without `--account` creates reviewable artifacts only. Run `process`
again with account flags to queue existing artifacts without re-rendering. Jobs
are unique per account and clip path. Reject with `review ID --reject`.

## Pipeline

```text
YouTube URL / channel / RSS
        -> audio download -> word transcription -> overlapping LLM chunks
        -> ranked non-overlapping moments -> video download only if needed
        -> silence intervals + shared timestamp mapping -> face-aware 9:16 crop
        -> highlighted ASS subtitles -> H.264/AAC MP4 + transcript/metadata manifest
        -> review -> per-account scheduled SQLite queue -> official API -> status polling
```

| Module | Responsibility |
| --- | --- |
| `config.py`, `models.py` | Validated configuration and timestamp/metadata schemas |
| `downloader.py` | yt-dlp subprocess, audio-first ingestion, 720p video cap, RSS/channel discovery |
| `transcriber.py` | Faster-Whisper CPU int8 / CUDA float16, or 10-minute Whisper API chunks |
| `llm.py`, `curator.py` | OpenRouter/OpenAI/Anthropic/Gemini adapters, JSON validation, boundary snapping, overlap suppression |
| `editor.py`, `framing.py` | FFmpeg silence detection, synchronized A/V cuts, face/mouth-motion framing, timed word highlights |
| `metadata.py` | US-English titles under 60 chars, descriptions, relevant hashtags |
| `scheduler.py` | SQLite WAL transactions, daily capacity, review, atomic claims, crash states |
| `publisher.py` | YouTube, TikTok, Instagram official APIs and processing polling |
| `pipeline.py`, `cli.py` | Durable artifacts, source deduplication, daemon and CLI |

Local transcription returns absolute word-level `start`, `end`, `text` values.
The staging manifest preserves the full transcript, source, selected moments,
scores, reasons, keywords, clip filenames and metadata. Temporary media and
render chunks are deleted when each episode completes or raises an exception.

## Configuration

All settings use the `CLIPPER_` prefix; see `.env.example`. For Claude use
`CLIPPER_LLM_PROVIDER=anthropic`, your key, and a model available to your account.
For Gemini use `gemini` and its model ID. Model names are configurable because
provider availability changes; the historical Gemini 1.5 / Claude 3.5 examples
are not hardcoded. Default: `openrouter` / `openrouter/free`. Explicit OpenAI
configuration still supports `gpt-4o-mini`.

### Ordered fallback: free OpenRouter -> Gemini -> paid OpenRouter

Create an API key in your OpenRouter account and set these values in `.env`:

```env
CLIPPER_LLM_PROVIDER=openrouter
CLIPPER_LLM_MODEL=openrouter/free
CLIPPER_OPENROUTER_API_KEY=YOUR_KEY_HERE
CLIPPER_LLM_FALLBACK_ENABLED=true
CLIPPER_GEMINI_API_KEY=YOUR_GEMINI_KEY_HERE
CLIPPER_GEMINI_FALLBACK_MODEL=gemini-2.5-flash
CLIPPER_OPENROUTER_PAID_MODEL=openai/gpt-4o-mini
CLIPPER_TRANSCRIPTION=local
```

The official `openrouter/free` router automatically chooses among available free
models, filtering for the request's capabilities, including JSON output. This
applies to both moment curation and metadata generation. Selection is not a
quality ranking and may vary between requests. No model list needs maintenance.
You can optionally pin an explicit model ID ending in `:free`; that disables
automatic model selection for the first stage. The primary OpenRouter model must
remain free; configure the final paid stage separately with
`CLIPPER_OPENROUTER_PAID_MODEL`.

Rate limits, provider outages and malformed JSON are retried up to four attempts;
each router retry may select another eligible free model. Each request starts at
the first stage and stops at the first valid JSON object, in this order:

1. Free OpenRouter (`openrouter/free`).
2. Gemini directly via Google's API, using its own API key.
3. Paid OpenRouter, using the same OpenRouter key and the configured paid model.

Fallback happens after exhausted retries or a non-retryable provider HTTP error
(for example invalid credentials or an unavailable model). Missing API keys skip
that stage with a log warning. Each configured stage makes at most four attempts;
if every stage fails, the operation fails. Explicit single-provider selections
(`openai`, `anthropic`, `gemini`) do not run this chain. Set
`CLIPPER_LLM_FALLBACK_ENABLED=false` to restrict requests to the primary provider.
Schema checks for curated moments and metadata still run after JSON parsing.

The third stage can consume OpenRouter credits; Gemini billing depends on your
Google project and quota. Free access is not unlimited. Local transcription avoids
the separate paid Whisper API; VPS hosting costs are unaffected. Failed monitored
episodes retry on the next scan. Logs show stage outcomes without credentials or
transcript content.

Existing `.env` files are not overwritten: replace their previous provider/model
values with the settings above. After pulling updates, rebuild/restart Docker with
`docker compose up -d --build`. Keep the API key out of Git and chat messages.

Reference: [OpenRouter Free Models Router](https://openrouter.ai/openrouter/free).

`CLIPPER_TRANSCRIPTION=local` uses Faster-Whisper; `api` uses OpenAI `whisper-1`.
The local model downloads on first use. CUDA mode requires a compatible NVIDIA
driver, CUDA/cuDNN runtime and CTranslate2 build; the provided Dockerfile is CPU
only. Set `CLIPPER_DEVICE=cpu` for predictable VPS deployment.

Per-account `proxy_env` and per-source `proxy_env` name environment variables
holding `http://user:password@host:port` or `socks5://...` URLs. Proxies route
traffic; they do not guarantee audience geography or bypass platform limits.
Account credentials are never committed or included in application logs.

## Publishing prerequisites

Each account must have its own authorized token, scopes and platform app access.
Obtain OAuth grants separately and inject tokens through `.env` or your secret
manager. This project does not implement an interactive OAuth consent server or
token renewal; rotate expiring tokens and restart the daemon. No browser profiles
are needed because adapters use official APIs.

* **YouTube:** `youtube.upload` for uploads and `youtube.readonly` for processing
  checks. Default privacy is `private`; set `public` only when intended. Uploads
  from unverified projects can be restricted to private by YouTube. Resumable
  upload protocol is used, but byte-offset recovery is not implemented; interrupted
  uploads require operator reconciliation.
* **TikTok:** `video.publish`, current creator info, a user-selected privacy value
  (`SELF_ONLY` for initial testing), and an eligible audited app for public posting.
  API audit and creator consent/UX requirements still apply; this CLI is not an
  audited TikTok client. Review each upload and confirm platform requirements
  before enabling automation. Adapter accepts MP4 up to 64 MiB. Duet, stitch and
  comments are disabled in the initial implementation.
* **Instagram:** professional account through Facebook Login, suitable page/account
  permissions, account ID and Graph API version. `public_media_base` must serve the
  corresponding MP4 files over publicly reachable HTTPS. Configure your media
  origin/CDN; files are not automatically transferred to object storage. The nginx
  snippet exposes MP4 files only. Grant its service user read/traverse permission
  deliberately; the systemd service defaults to private files. Containers are
  polled until ready, then published. Instagram publication has no private-post mode.

Publishing APIs and permissions are external dependencies. Live credentialed
transcription, LLM requests, downloads and social uploads must be verified in your
deployment; local tests use synthetic media and mocked HTTP responses.

## Scheduling and operations

New accounts reserve at most one post per local calendar day. Accounts marked
`warmed=true` reserve up to `daily_limit` (1–4, default 3). Slots have randomized
spacing of at least 3 hours and use each account's IANA timezone. These are local
operational caps, not guarantees against platform restrictions or a method of
account warming. Reconfigure accounts only after reviewing already reserved jobs.

SQLite uses WAL and `BEGIN IMMEDIATE` for reservations and claims. One daemon per
data directory is enforced by an OS lock; rendering and publishing run as separate
async tasks. Do not place SQLite on a network filesystem or run replicated workers
against it. Source ingestion starts with the newest `limit` episodes, including
existing ones, on the first run. Failed episodes retry on later scans.

Queue states: `review -> queued -> uploading -> processing -> published`;
`rejected`, `failed`, and `uncertain` require inspection. Overdue queued jobs are
rescheduled, preventing catch-up bursts. A crash during upload becomes `uncertain`
and blocks that account to avoid duplicate posts. Check the platform, then stop
the daemon and resolve:

```bash
python -m clipper reconcile 7 published --remote-id VERIFIED_PLATFORM_ID
# Or processing with a known upload/container id, or failed after confirming no post.
```

Do not resolve as failed unless you have verified that nothing was published.
Exactly-once delivery cannot be guaranteed across third-party APIs. Polling errors
retain processing state except Instagram's publish-capable poll, which becomes
uncertain until reconciled. Logs rotate at 10 MB with 14-day retention. Exception
types are logged without provider response bodies or tokens.

Back up SQLite with its backup API (or while stopped), plus staging manifests/media.
Staging files are retained for review and audit; monitor disk space and archive
completed clips manually. Do not delete clips referenced by pending jobs. A forced
OS kill may leave `episode-*` or `render-*` directories; remove only abandoned
directories with the worker stopped. Normal cancellation cleans temporary renders.

## Framing and subtitle limitations

OpenCV samples face and mouth motion, discounts upper-face movement, follows the
likely speaking face with a smoothed dynamic crop, uses stacked panels when two
mouths consistently move, and center crop for ambiguous scenes. Crop changes are
mapped through the same silence-cut timeline as subtitles. This visual heuristic
is not an audio-visual diarization model: head movements, dubbing and camera cuts
can mislead it. MediaPipe is pinned as requested; the default strategy uses OpenCV.

Subtitles show four-word phrases with the current word yellow, bold uppercase,
black outline/shadow and a size emphasis. Silence over 0.4 s is removed with small
breath handles; the same intervals cut both audio and video and remap subtitles.
If silence removal would shorten a clip below the configured minimum, its original
duration is preserved. Review fast speech, overlapping voices and ASR errors.

## Deployment

For Docker, create configuration files first, then:

```bash
docker compose up -d --build
docker compose logs -f
docker compose exec clipper python -m clipper queue
docker compose exec clipper python -m clipper review 1 --approve
```

The non-root container has a read-only root filesystem, named data/model volumes,
an init process, dropped capabilities, and 4 CPU / 6 GB limits. Mount or export
the data volume to inspect artifacts. Public Instagram media hosting is separate.

For systemd, install the project/venv at `/opt/clipper`, create a `clipper` service
user, place configuration there, and install `deploy/clipper.service` under
`/etc/systemd/system/`. Then `systemctl daemon-reload` and
`systemctl enable --now clipper`. Avoid running Docker and systemd against the
same queue simultaneously.

Direct dependencies are version-pinned in `requirements.txt`. For fully frozen
transitive packages, run `sh scripts/lock.sh` on the target Ubuntu/Python version
and install `requirements.lock` with `--require-hashes`. A Windows resolution
is not a Linux lock. Keep yt-dlp current through a reviewed dependency update;
YouTube extractor behavior can change independently of this repository.

## Validation

```bash
pip install -r requirements-dev.txt
ruff check src tests
pytest -q
python -m clipper doctor
```

Tests cover timeline remapping, subtitle escaping, segment selection, quota and
concurrent claim behavior, crash recovery, reviewed scheduling, API errors and
streaming upload requests, plus a real synthetic FFmpeg render. GitHub Actions
runs the suite on Ubuntu. No live social posts are made by tests.

## API references

* [Faster-Whisper word timestamps](https://github.com/SYSTRAN/faster-whisper)
* [FFmpeg filters and libass](https://ffmpeg.org/ffmpeg-filters.html)
* [YouTube videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)
* [TikTok Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)
* [Instagram publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)

## GitHub

No credentials, recordings or generated clips belong in Git. Once a destination
repository is configured, push the source commit with `git push -u origin HEAD`.
