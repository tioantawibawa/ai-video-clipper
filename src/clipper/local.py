"""Local editing with manual cuts, source-aware transcript caching, and no uploads."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from loguru import logger
from .config import Settings
from .curator import curate
from .editor import render
from .metadata import generate
from .models import Metadata, Moment, Word
from .pipeline import worker_lock
from .process import run
from .transcriber import transcribe


def fingerprint(media):
    digest = hashlib.sha256()
    with media.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)


def read_words(path, duration):
    words = [Word.model_validate(w) for w in json.loads(path.read_text(encoding="utf-8"))]
    if not words or any(b.start < a.start for a, b in zip(words, words[1:])):
        raise ValueError("Transcript must contain chronological words")
    if max(w.end for w in words) > duration + .2:
        raise ValueError("Transcript exceeds source duration")
    return words


def parser():
    p = argparse.ArgumentParser(description="Edit local video for review; never uploads")
    p.add_argument("media", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--source", default="", help="Original URL/reference")
    p.add_argument("--credit", default="", help="Source attribution")
    p.add_argument("--start", type=float, help="Manual start in seconds; requires --end")
    p.add_argument("--end", type=float)
    p.add_argument("--title", help="Required for manual mode; under 60 characters")
    p.add_argument("--description", default="")
    p.add_argument("--transcript", type=Path, help="Word JSON for this exact media timeline")
    p.add_argument("--whisper-model")
    p.add_argument("--device", choices=["cpu", "cuda", "auto"])
    p.add_argument("--cpu-threads", type=int)
    return p


async def process(args):
    if (args.start is None) != (args.end is None):
        raise ValueError("Provide both --start and --end")
    if not args.media.is_file():
        raise ValueError("Input media does not exist")
    cfg = Settings(**{k: getattr(args, k) for k in ("whisper_model", "device", "cpu_threads")
                      if getattr(args, k) is not None}, max_clips=1)
    manual = args.start is not None
    if manual:
        if not args.title:
            raise ValueError("Manual mode requires --title")
        moment = Moment(start=args.start, end=args.end, title=args.title, hook_score=0,
                        reason="Manual selection; hook score not evaluated", keywords=[])
        if not cfg.min_duration <= moment.end - moment.start <= cfg.max_duration:
            raise ValueError("Manual cut must respect configured duration (default 30-60s)")
        metadata = Metadata(title=args.title, description=args.description, hashtags=[])
    info = json.loads(await run("ffprobe", "-v", "error", "-show_format", "-show_streams",
                                "-of", "json", str(args.media.resolve())))
    if not all(any(s.get("codec_type") == kind for s in info["streams"]) for kind in ("video", "audio")):
        raise ValueError("Input requires both video and audio")
    duration = float(info["format"]["duration"])
    if manual and moment.end > duration:
        raise ValueError("Manual cut exceeds source duration")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with worker_lock(output):
        if any((output / n).exists() for n in ("clip.mp4", "manifest.json")):
            raise ValueError("Finished output exists; choose a new --output directory")
        identity = {"sha256": await asyncio.to_thread(fingerprint, args.media),
                    "transcription": cfg.transcription, "whisper_model": cfg.whisper_model,
                    "device": cfg.device}
        cache, transcript = output / "transcript-cache.json", output / "transcript.json"
        if args.transcript:
            words = read_words(args.transcript, duration)
        elif cache.exists() and transcript.exists() and json.loads(cache.read_text()) == identity:
            logger.info("Reusing matching transcript cache")
            words = read_words(transcript, duration)
        else:
            logger.info("Transcribing local media")
            with TemporaryDirectory(prefix="transcribe-", dir=output) as temp:
                words = await transcribe(args.media.resolve(), Path(temp), cfg)
                read_words(Path(temp) / "transcript.json", duration)
            write_json(transcript, [w.model_dump() for w in words])
            write_json(cache, identity)
        if not manual:
            moments = await curate(words, cfg)
            if not moments:
                raise ValueError("No qualifying moment; use manual boundaries")
            moment = moments[0]
            if moment.end > duration:
                raise ValueError("Selected moment exceeds source duration")
            metadata = await generate(moment, words, cfg)
        payload = metadata.model_dump()
        attribution = " | ".join(x for x in (args.credit, args.source) if x)
        if attribution:
            payload["description"] += "\nSource: " + attribution
        metadata = Metadata.model_validate(payload)
        write_json(output / "selection.json", moment.model_dump())
        logger.info("Rendering {:.2f}-{:.2f}s", moment.start, moment.end)
        with TemporaryDirectory(prefix="stage-", dir=output) as temp:
            rendered = Path(temp) / "clip.mp4"
            await render(args.media.resolve(), moment, words, rendered, cfg)
            rendered.replace(output / "clip.mp4")
        write_json(output / "manifest.json", {
            "source": args.source or str(args.media.resolve()), "source_sha256": identity["sha256"],
            "selection_mode": "manual" if manual else "llm", "moment": moment.model_dump(),
            "metadata": metadata.model_dump(), "file": "clip.mp4", "review_required": True, "published": False})
        logger.info("Staged: {}", output / "clip.mp4")
    return output / "clip.mp4"


def main():
    args = parser().parse_args()
    try:
        asyncio.run(process(args))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        logger.error("Local editing failed ({}); check input/configuration. Cached transcript retained.", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
