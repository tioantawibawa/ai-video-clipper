# Laptop editing and VPS publication

The VPS performs research, analytics, ordinary comment replies and scheduled
YouTube publication. Set `produce:false` in its manager config and leave its
source-monitor list empty and `CLIPPER_MANAGER_RENDER=false` to avoid rendering
on the VPS. Set `reply_mode:auto`
only after the account owner authorizes automatic replies. Ordinary new comments
may then be answered without review; the LLM holds unsuitable comments. Unknown
send outcomes never retry automatically. Ads still require budget approval.

The laptop worker uses the existing local transcriber, curator and FFmpeg editor.
It only reads supplied media files; it does not solve CAPTCHA, extract browser
sessions or retry blocked YouTube downloads. The laptop must be awake and
connected while editing/transferring. The VPS can keep uploading previously
delivered clips when the laptop is offline.

## Setup

Runtime configuration belongs in `data/local-worker.json` (ignored by Git):

```json
{
  "inbox": "C:/PATH/ai-video-clipper/data/local-inbox",
  "output": "C:/PATH/ai-video-clipper/data/local-edits",
  "env_file": "C:/PATH/ai-video-clipper/credentials/local-worker.env",
  "ssh_key": "C:/PATH/ai-video-clipper/credentials/clipper_vps",
  "known_hosts": "C:/PATH/ai-video-clipper/credentials/clipper_known_hosts",
  "host": "ubuntu@43.156.75.116",
  "remote_outbox": "/home/ubuntu/ai-video-clipper/data/youtube-outbox",
  "poll_seconds": 60
}
```

The local environment file uses the same `CLIPPER_` LLM and rendering variables.
Preserve free OpenRouter -> Gemini -> paid OpenRouter. Use a dedicated SSH key
installed with the owner's explicit approval, and pin the VPS host key. Do not
commit keys, cookies or API secrets. FFmpeg must be on the worker's PATH.

If the owner explicitly approves moving LLM keys from the VPS to this laptop,
`python -m clipper.local_worker --config data/local-worker.json --sync-env` copies
only whitelisted LLM settings through pinned SSH. It excludes YouTube/account
credentials. On Windows, restrict the resulting file's ACL to the owner and
SYSTEM; Python's chmod alone does not protect a Windows file. Restart the local
worker after changing its environment file.

Start with `powershell -File scripts/start-local-worker.ps1`; use `-Once` for
one pass. This script does not register automatic startup after a laptop reboot.

## Submit a source

Place legally reusable original media in `data/local-inbox/source.mp4`. Then
create `data/local-inbox/first.request.json` with actual source attribution and
documented reuse permission/Creative Commons source information:

```json
{
  "media": "source.mp4",
  "source": "https://www.youtube.com/watch?v=SOURCE_ID",
  "rights_note": "Creative Commons source verified, verification reference",
  "approved": true,
  "credit": "Source title, creator, URL, CC BY license, excerpted/cropped/subtitled"
}
```

Creating this local request with `approved:true` authorizes publication of its
result. Do not create requests for already uploaded clips. Optional `start`,
`end`, `title` select a manual 30-60 second moment; optional `transcript` supplies
word JSON for the exact source timeline. File paths must stay within the inbox.

The worker renders one vertical captioned clip locally, verifies its content
hash remotely, then promotes the outbox manifest last. Transfer is a queue
submission, not confirmation of public publication. The VPS retains the limit
of one public video/day in America/New_York. Content-hash filenames deduplicate
the same rendered media in that outbox.

Receipts and logs live in `data/local-edits`. Failed/interrupted requests are
held; inspect both remote queue and receipt before retrying. Do not delete a
receipt blindly after a transfer timeout, as the clip may already be queued.

## Comment polling

Install `deploy/clipper-comments.service` and `.timer` on the VPS. The timer runs
about every 30 minutes with up to three minutes jitter. `manager comments`
does not repeat daily research or issue production tickets. Existing drafts
remain unchanged until explicitly sent; enabling auto mode applies to new
ordinary replies. Rejected/uncertain replies remain held.
