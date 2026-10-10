# Reviewed clip batches

Prepare clips locally, inspect their original audio, subtitles and framing, then
copy only completed outputs into a dedicated directory on the VPS. Do not place
them in the automatic outbox: that scanner assigns the first available slot.

Each manifest contains `metadata`, `qa_approved: true` and `clip_sha256`. Preserve
the actual source license and any publisher permission URL; never relabel a
Standard YouTube source as Creative Commons. A supplied source for a finite batch
does not change the permanent Ronaldo-only daily research policy.

Create `batch.json` as a list of entries:

```json
[
  {
    "clip": "part-1/clip.mp4",
    "manifest": "part-1/manifest.json",
    "publish_at": "2026-10-11T19:10:00+07:00"
  }
]
```

Run from the VPS checkout:

```sh
PYTHONPATH=src .venv/bin/python scripts/schedule_batch.py /absolute/batch/batch.json
```

Reservations commit together. An occupied day, changed checksum, unreviewed media,
duplicate day, past timestamp or unexpected existing schedule rolls back the batch.
Repeating the same plan returns existing queue IDs, without creating duplicates.
The daemon uploads at each reserved time; upload and processing take additional time.
These are VPS queue reservations, not videos already uploaded to YouTube.
