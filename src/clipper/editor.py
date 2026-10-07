import asyncio
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import Settings
from .framing import framing
from .models import Moment, Word
from .process import run


def keep_intervals(log: str, duration: float) -> list[tuple[float, float]]:
    silences = []
    start = None
    for kind, value in re.findall(r"silence_(start|end):\s*([\d.]+)", log):
        if kind == "start":
            start = float(value)
        elif start is not None:
            silences.append((max(0, start), min(duration, float(value))))
            start = None
    if start is not None:
        silences.append((start, duration))
    intervals, cursor = [], 0.0
    for start, end in silences:
        # Retain small breath handles at each edit.
        left, right = min(duration, start + .08), max(0, end - .08)
        if left > cursor:
            intervals.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < duration:
        intervals.append((cursor, duration))
    return [(a, b) for a, b in intervals if b - a > .02]


def remap_words(words: list[Word], start: float, intervals) -> list[Word]:
    output, offset = [], 0.0
    for a, b in intervals:
        for word in words:
            left, right = max(word.start - start, a), min(word.end - start, b)
            if right > left:
                output.append(Word(start=offset + left - a, end=offset + right - a, text=word.text))
        offset += b - a
    return output


def ass_time(seconds: float) -> str:
    centis = round(seconds * 100)
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def safe_text(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ").upper()


def subtitles(words: list[Word], path: Path, cfg: Settings, annotation=None):
    color = cfg.highlight[4:6] + cfg.highlight[2:4] + cfg.highlight[0:2]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {cfg.width}
PlayResY: {cfg.height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg.font},{cfg.font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,{int(cfg.width*.075)},{int(cfg.width*.075)},{int(cfg.height*.16)},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    if annotation and words:
        events.append(f"Dialogue: 1,0:00:00.00,{ass_time(max(w.end for w in words))},Default,,0,0,0,,{{\\an8\\pos({cfg.width//2},38)\\fs38}}{safe_text(annotation)}")
    for i in range(0, len(words), 4):
        phrase = words[i:i + 4]
        for j, word in enumerate(phrase):
            parts = [((f"{{\\c&H{color}&\\fscx108\\fscy108}}{safe_text(w.text)}{{\\r}}")
                      if k == j else safe_text(w.text)) for k, w in enumerate(phrase)]
            events.append(f"Dialogue: 0,{ass_time(word.start)},{ass_time(word.end)},Default,,0,0,0,,{' '.join(parts)}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


async def render(media: Path, moment: Moment, words: list[Word], output: Path, cfg: Settings, frame_filter=None, annotation=None):
    duration = moment.end - moment.start
    log = await run("ffmpeg", "-nostdin", "-hide_banner", "-ss", str(moment.start), "-t", str(duration),
                    "-i", str(media), "-vn", "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-")
    intervals = keep_intervals(log, duration)
    # Preserve requested minimum length when aggressive cuts would make it too short.
    if sum(b - a for a, b in intervals) < cfg.min_duration:
        intervals = [(0, duration)]
    frame_filter = frame_filter or await asyncio.to_thread(framing, media, moment, cfg, intervals)
    with TemporaryDirectory(prefix="render-", dir=output.parent) as folder:
        temp = Path(folder)
        subtitles(remap_words(words, moment.start, intervals), temp / "captions.ass", cfg, annotation)
        filters, inputs = [], []
        for i, (a, b) in enumerate(intervals):
            filters += [f"[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]",
                        f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}]"]
            inputs += [f"[v{i}][a{i}]"]
        filters.append("".join(inputs) + f"concat=n={len(intervals)}:v=1:a=1[joined][audio]")
        filters.append(f"[joined]{frame_filter},ass=captions.ass[video]")
        (temp / "filters.txt").write_text(";".join(filters), encoding="utf-8")
        await run("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                  "-ss", str(moment.start), "-t", str(duration), "-i", str(media.resolve()),
                  "-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-threads", str(cfg.cpu_threads),
                  "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "128k",
                  "-movflags", "+faststart", str(output.resolve()), cwd=temp, timeout=cfg.subprocess_timeout)
    info = json.loads(await run("ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)))
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    if (video["width"], video["height"]) != (cfg.width, cfg.height):
        raise RuntimeError("Rendered dimensions mismatch")
    if not cfg.min_duration - .2 <= float(info["format"]["duration"]) <= cfg.max_duration + .2:
        raise RuntimeError("Rendered duration outside configured limits")
