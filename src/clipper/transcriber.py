import asyncio
import json
from pathlib import Path

import httpx

from .config import Settings
from .models import Word
from .process import run


def local_transcribe(path: Path, cfg: Settings) -> list[Word]:
    import ctranslate2
    from faster_whisper import WhisperModel

    device = cfg.device
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
    model = WhisperModel(cfg.whisper_model, device=device,
                         compute_type="float16" if device == "cuda" else "int8",
                         cpu_threads=cfg.cpu_threads)
    segments, _ = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
    return [Word(start=w.start, end=w.end, text=w.word.strip())
            for s in segments for w in (s.words or []) if w.end > w.start and w.word.strip()]


async def transcribe(media: Path, folder: Path, cfg: Settings) -> list[Word]:
    if cfg.transcription == "local":
        words = await asyncio.to_thread(local_transcribe, media, cfg)
    else:
        key = cfg.openai_api_key.get_secret_value()
        if not key:
            raise ValueError("OpenAI key required for Whisper API")
        # Each 10-minute mono MP3 is far below the API's 25 MB request limit.
        await run("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                  "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
                  "-f", "segment", "-segment_time", "600", str(folder / "speech-%04d.mp3"))
        words = []
        async with httpx.AsyncClient(timeout=180) as client:
            for i, chunk in enumerate(sorted(folder.glob("speech-*.mp3"))):
                try:
                    with chunk.open("rb") as handle:
                        response = await client.post("https://api.openai.com/v1/audio/transcriptions",
                            headers={"Authorization": f"Bearer {key}"},
                            data={"model": "whisper-1", "response_format": "verbose_json",
                                  "timestamp_granularities[]": "word"},
                            files={"file": (chunk.name, handle, "audio/mpeg")})
                    response.raise_for_status()
                    words.extend(Word(start=w["start"] + i * 600, end=w["end"] + i * 600,
                                      text=w["word"]) for w in response.json()["words"]
                                 if w["end"] > w["start"])
                finally:
                    chunk.unlink(missing_ok=True)
    if not words:
        raise ValueError("No speech found")
    (folder / "transcript.json").write_text(json.dumps([w.model_dump() for w in words]), encoding="utf-8")
    return words
