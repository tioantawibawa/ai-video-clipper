import asyncio
import json
import shutil

import pytest

from clipper.config import Settings
from clipper.editor import render
from clipper.models import Moment, Word
from clipper.process import run


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
@pytest.mark.parametrize("mode", ["center", "split"])
def test_real_render_with_silence_and_subtitles(tmp_path, monkeypatch, mode):
    cfg = Settings(width=180, height=320, font_size=18, min_duration=2, max_duration=6)
    source, output = tmp_path / "source.mp4", tmp_path / "clip.mp4"
    if mode == "split":
        monkeypatch.setattr("clipper.framing.choose_focus", lambda _: ("split", [.25, .75]))

    async def scenario():
        await run("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                  "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=6",
                  "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                  "-af", "volume=enable='between(t,2,4)':volume=0",
                  "-c:v", "libx264", "-c:a", "aac", str(source))
        await render(source, Moment(start=0, end=6, title="Test", hook_score=9, reason="Test", keywords=[]),
                     [Word(start=.5, end=1, text="HELLO"), Word(start=4.5, end=5, text="WORLD")], output, cfg)
        return json.loads(await run("ffprobe", "-v", "error", "-show_format", "-of", "json", str(output)))

    info = asyncio.run(scenario())
    assert 4 < float(info["format"]["duration"]) < 4.6
    assert not list(tmp_path.glob("render-*"))
