import asyncio
import json

import pytest

from clipper import local
from clipper.models import Word


def setup(tmp_path, monkeypatch):
    media = tmp_path / "input.mp4"
    media.write_bytes(b"source-one")
    transcript = tmp_path / "words.json"
    transcript.write_text(json.dumps([dict(start=1, end=2, text="Hello")]))
    async def probe(*args):
        return json.dumps({"format": {"duration": "90"}, "streams": [
            {"codec_type": "video"}, {"codec_type": "audio"}]})
    async def render(media, moment, words, output, cfg):
        output.write_bytes(b"rendered")
    monkeypatch.setattr(local, "run", probe)
    monkeypatch.setattr(local, "render", render)
    args = local.parser().parse_args([str(media), "--output", str(tmp_path / "out"),
        "--start", "0", "--end", "40", "--title", "Local test"])
    return args, transcript


def test_manual_no_llm_and_preserves_completed_output(tmp_path, monkeypatch):
    args, transcript = setup(tmp_path, monkeypatch)
    args.transcript = transcript
    async def forbidden(*args):
        pytest.fail("Manual mode must not call an LLM or transcriber when transcript supplied")
    monkeypatch.setattr(local, "curate", forbidden)
    monkeypatch.setattr(local, "generate", forbidden)
    monkeypatch.setattr(local, "transcribe", forbidden)
    result = asyncio.run(local.process(args))
    manifest = json.loads((result.parent / "manifest.json").read_text())
    assert manifest["published"] is False
    assert manifest["selection_mode"] == "manual"
    assert "NASA" not in manifest["metadata"]["description"]
    with pytest.raises(ValueError, match="Finished output"):
        asyncio.run(local.process(args))
    assert result.read_bytes() == b"rendered"


def test_cache_invalidated_for_changed_media(tmp_path, monkeypatch):
    args, _ = setup(tmp_path, monkeypatch)
    calls = []
    async def transcribe(media, folder, cfg):
        calls.append(media.read_bytes())
        words = [Word(start=1, end=2, text="Hello")]
        local.write_json(folder / "transcript.json", [w.model_dump() for w in words])
        return words
    async def fail(*args):
        raise RuntimeError("render interrupted")
    monkeypatch.setattr(local, "transcribe", transcribe)
    monkeypatch.setattr(local, "render", fail)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            asyncio.run(local.process(args))
    assert len(calls) == 1
    args.media.write_bytes(b"different-source")
    with pytest.raises(RuntimeError):
        asyncio.run(local.process(args))
    assert len(calls) == 2
    assert not list(args.output.glob("stage-*"))


def test_invalid_boundaries_before_render(tmp_path, monkeypatch):
    args, transcript = setup(tmp_path, monkeypatch)
    args.transcript = transcript
    args.start, args.end = 70, 110
    with pytest.raises(ValueError, match="exceeds source"):
        asyncio.run(local.process(args))
