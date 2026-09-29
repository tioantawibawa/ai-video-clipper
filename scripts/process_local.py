"""Stage one local-media test without enqueueing or publishing anything."""
import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from clipper.config import Settings
from clipper.curator import curate
from clipper.editor import render
from clipper.metadata import generate
from clipper.models import Word
from clipper.transcriber import transcribe


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    cfg = Settings(max_clips=1, device="cpu", cpu_threads=1, whisper_model="tiny.en")
    args.output.mkdir(parents=True, exist_ok=True)
    transcript = args.output / "transcript.json"
    if transcript.exists():
        words = [Word.model_validate(w) for w in json.loads(transcript.read_text())]
    else:
        print("Transcribing", flush=True)
        words = await transcribe(args.media, args.output, cfg)
    print(f"Transcript: {len(words)} words", flush=True)
    moments = await curate(words, cfg)
    if not moments:
        raise RuntimeError("No qualifying complete moment found")
    moment = moments[0]
    metadata = await generate(moment, words, cfg)
    metadata.description += "\nSource: NASA, Changing Your Perspective (Down to Earth). " + args.source
    (args.output / "selection.json").write_text(json.dumps(moment.model_dump(), indent=2))
    with TemporaryDirectory(prefix="render-", dir=args.output) as temporary:
        path = Path(temporary) / "clip.mp4"
        print("Rendering selected moment", flush=True)
        await render(args.media, moment, words, path, cfg)
        path.replace(args.output / "clip.mp4")
    (args.output / "manifest.json").write_text(json.dumps({
        "source": args.source, "moment": moment.model_dump(), "metadata": metadata.model_dump(),
        "file": "clip.mp4", "review_required": True, "published": False,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Staged: " + str(args.output / "clip.mp4"), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
