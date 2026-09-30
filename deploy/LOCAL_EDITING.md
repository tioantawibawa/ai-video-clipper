# Local video editing

Run from the repository with its virtual environment installed:

```sh
python -m clipper.local /path/to/input.mp4 --output data/local/run-01 --source "original URL" --credit "Creator name"
```

PowerShell: replace `python` with `.\.venv\Scripts\python.exe`.
Ubuntu: replace it with `.venv/bin/python`. The previous
`python scripts/process_local.py ...` entry point still works.

Automatic mode transcribes, selects one 30-60 second moment with the configured
LLM fallback order, generates metadata and renders a vertical clip. It reads `.env`
settings instead of forcing CPU/tiny.en. On a small VPS use
`--whisper-model tiny.en --device cpu --cpu-threads 1`.

For an exact edit with no LLM calls:

```sh
python -m clipper.local source.mp4 --output data/local/run-02 --start 246.58 --end 302.78 --title "How Space Changed the Way She Sees Earth" --credit "NASA" --source "https://plus.nasa.gov/video/down-to-earth-changing-your-perspective/" --transcript data/nasa-test/review/transcript.json
```

The example boundaries apply to the original NASA source, not an already trimmed
clip. Manual mode still transcribes unless `--transcript` supplies word-level JSON
for that exact source timeline. Correct subtitle text in a copy of the JSON and
pass that copy to rerender. Manual hook score 0 means not evaluated.

Outputs: `clip.mp4`, `manifest.json`, `selection.json`, and a transcript/cache when
transcription was performed. Source credit is added to the description, not burned
into the picture. Finished clips are never overwritten: choose a new output folder.
Interrupted runs can reuse a transcript only when the media hash and transcription
settings match. Temporary render files are removed after success or failure.

No uploads or publish queue entries are made. Preview the subtitles, framing and
sentence boundaries before authorizing a separate upload. Automatic selection
does not guarantee complete sentences, so manual correction remains available.
