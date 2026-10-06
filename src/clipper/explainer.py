"""Render original narrated tactical explainers locally; never publish implicitly."""
import argparse
import asyncio
import hashlib
import json
import math
import shutil
import textwrap
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from .models import Metadata, Word
from .process import run


class Scene(BaseModel):
    heading: str = Field(min_length=1, max_length=65)
    points: list[str] = Field(min_length=1, max_length=5)
    narration: str = Field(min_length=20)
    mode: str = Field(default="box", pattern="^(box|press|space|timeline|decision|news)$")


class Episode(BaseModel):
    metadata: Metadata
    scenes: list[Scene] = Field(min_length=1)
    vertical: bool = False
    voice: str = "en-US-GuyNeural"
    checked_on: str
    sources: list[str] = Field(min_length=1)


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def ass_time(seconds):
    cs = max(0, round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def subtitles(words, path, width, height):
    font_size = 45 if width > height else 54
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H0000FFFF,&H00101820,&H70000000,-1,0,0,0,100,100,0,0,1,3,1,2,70,70,{120 if width > height else 330},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for pos in range(0, len(words), 7):
        group = words[pos:pos + 7]
        start, end = group[0].start, group[-1].end
        text = " ".join("{\\kf" + str(max(1, round((w.end-w.start)*100))) + "}" +
                        w.text.replace("\\", "").replace("{", "").replace("}", "") for w in group)
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")
    path.write_text(header + "\n".join(events), encoding="utf-8")


def font(size, bold=False):
    from PIL import ImageFont
    candidates = [Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
                       "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise RuntimeError("Install Arial or DejaVu Sans fonts")


def card(scene, path, vertical, phase, checked_on):
    from PIL import Image, ImageDraw
    w, h = (1080, 1920) if vertical else (1920, 1080)
    im = Image.new("RGB", (w, h), "#0b1422")
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w, 14), fill="#ed3d45")
    d.text((65, 42), "THE FOOTBALL QUESTION", font=font(27, True), fill="#f4c64b")
    heading = textwrap.wrap(scene.heading.upper(), 25 if vertical else 48)
    for n, line in enumerate(heading):
        d.text((65, 105+n*65), line, font=font(53, True), fill="white")
    if scene.mode == "news":
        # Original news graphics: no archival photo presented as today's footage.
        sx = w//2
        top = 380 if vertical else 270
        scale = 1.5 if vertical else 1.0
        jersey = [(-150,0),(-70,35),(70,35),(150,0),(270,95),(215,200),(140,150),
                  (140,440),(-140,440),(-140,150),(-215,200),(-270,95)]
        d.polygon([(sx+x*scale,top+y*scale) for x,y in jersey],fill="#df3446")
        d.text((sx-90*scale,top+140*scale),"7",font=font(round(250*scale),True),fill="white")
        y = 1140 if vertical else 755
        point = scene.points[phase % len(scene.points)]
        for n,line in enumerate(textwrap.wrap(point,32 if vertical else 60)):
            box = d.textbbox((0,0),line,font=font(42,True))
            d.text(((w-(box[2]-box[0]))/2,y+n*58),line,font=font(42,True),fill="#f4c64b")
        d.text((65,h-70),f"NEWS EXPLAINER | RTP | CHECKED {checked_on}",font=font(21),fill="#9eafc1")
        im.save(path)
        return
    top = 330 if vertical else 255
    left, right, bottom = 65, (w-65 if vertical else 1160), (1210 if vertical else 815)
    d.rounded_rectangle((left, top, right, bottom), radius=20, fill="#123d38")
    d.rectangle((left+25, top+25, right-25, bottom-25), outline="#9bd4bd", width=3)
    cx, cy = (left+right)//2, (top+bottom)//2
    d.line((cx, top+25, cx, bottom-25), fill="#9bd4bd", width=3)
    d.ellipse((cx-70, cy-70, cx+70, cy+70), outline="#9bd4bd", width=3)
    d.rectangle((right-155, cy-150, right-25, cy+150), outline="#9bd4bd", width=3)
    # Illustrative positions, not tracking data or a recreation of a real match.
    attack = [(0.38, .25), (.45, .75), (.58, .49), (.74, .23), (.76, .78), (.85, .5)]
    defense = [(.69, .15), (.72, .38), (.73, .61), (.70, .87), (.59, .31), (.58, .72)]
    for i, (x, y) in enumerate(defense):
        px, py = left+x*(right-left), top+y*(bottom-top)
        d.ellipse((px-21, py-21, px+21, py+21), fill="#7297b5", outline="white", width=2)
    striker = (.85, .5)
    if scene.mode == "press":
        striker = (.75-phase*.017, .5)
    elif scene.mode == "space":
        striker = (.84-phase*.025, .50+(phase%3-1)*.08)
    for i, (x, y) in enumerate(attack):
        if i == 5:
            x, y = striker
        px, py = left+x*(right-left), top+y*(bottom-top)
        d.ellipse((px-23, py-23, px+23, py+23), fill="#ef4250", outline="white", width=2)
        if i == 5:
            d.text((px-10, py-17), "9", font=font(26, True), fill="white")
    bx, by = left+.76*(right-left), top+.78*(bottom-top)
    tx, ty = left+striker[0]*(right-left), top+striker[1]*(bottom-top)
    d.line((bx, by, tx, ty), fill="#f4c64b", width=7)
    angle = math.atan2(ty-by, tx-bx)
    d.polygon([(tx, ty), (tx-25*math.cos(angle-.5), ty-25*math.sin(angle-.5)),
               (tx-25*math.cos(angle+.5), ty-25*math.sin(angle+.5))], fill="#f4c64b")
    d.ellipse((bx-11, by-11, bx+11, by+11), fill="white")
    label_x, label_y = (70, 1250) if vertical else (1220, 300)
    point = scene.points[phase % len(scene.points)]
    for n, line in enumerate(textwrap.wrap(point, 31 if vertical else 22)):
        d.text((label_x, label_y+n*56), line, font=font(43, True), fill="#f4c64b")
    d.text((65, h-70), f"TACTICAL ILLUSTRATION  |  CHECKED {checked_on}", font=font(21), fill="#9eafc1")
    im.save(path)


async def speech(scene, output, voice):
    import edge_tts
    key = hashlib.sha256((voice + scene.narration).encode()).hexdigest()
    audio, timings = output / f"{key}.mp3", output / f"{key}.words.json"
    if audio.is_file() and timings.is_file():
        return audio, [Word.model_validate(w) for w in json.loads(timings.read_text())]
    temp = audio.with_suffix(".part")
    words = []
    try:
        with temp.open("wb") as stream:
            async for item in edge_tts.Communicate(scene.narration, voice, boundary="WordBoundary").stream():
                if item["type"] == "audio":
                    stream.write(item["data"])
                elif item["type"] == "WordBoundary":
                    words.append(Word(start=item["offset"]/1e7,
                                      end=(item["offset"]+item["duration"])/1e7, text=item["text"]))
        if not words or temp.stat().st_size == 0:
            raise RuntimeError("TTS did not return audio and word timestamps")
        temp.replace(audio)
        dump(timings, [w.model_dump() for w in words])
    finally:
        temp.unlink(missing_ok=True)
    return audio, words


async def render(plan, output):
    episode = Episode.model_validate_json(plan.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "speech"
    cache.mkdir(exist_ok=True)
    work = output / "render-work"
    work.mkdir(exist_ok=True)
    w, h = (1080, 1920) if episode.vertical else (1920, 1080)
    chunks, chapters, total = [], [], 0.0
    try:
        for index, scene in enumerate(episode.scenes):
            audio, words = await speech(scene, cache, episode.voice)
            duration = float(json.loads(await run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "json", str(audio)))["format"]["duration"])
            chapters.append({"start": total, "heading": scene.heading})
            total += duration
            captions = work / "captions.ass"
            subtitles(words, captions, w, h)
            count = max(1, math.ceil(duration / 8))
            frames = []
            for phase in range(count):
                name = f"card-{phase}.png"
                card(scene, work / name, episode.vertical, phase, episode.checked_on)
                frames.extend([f"file '{name}'", f"duration {duration/count:.6f}"])
            frames.append(f"file 'card-{count-1}.png'")
            (work / "frames.txt").write_text("\n".join(frames), encoding="utf-8")
            chunk = work / f"scene-{index:03}.mp4"
            await run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                      "-i", "frames.txt", "-i", str(audio.resolve()), "-vf",
                      "fps=24,ass=captions.ass", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                      "-threads", "4", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                      "-t", str(duration), str(chunk.resolve()), cwd=work)
            chunks.append(chunk)
            logger.info("Rendered scene {}/{}: {}", index+1, len(episode.scenes), scene.heading)
        if episode.vertical and total > 45:
            raise ValueError(f"Short exceeds agreed 45 seconds ({total:.1f}s); shorten narration")
        (work / "chunks.txt").write_text("\n".join(f"file '{p.name}'" for p in chunks), encoding="utf-8")
        clip = output / "video.mp4"
        await run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                  "-i", "chunks.txt", "-c", "copy", "-movflags", "+faststart", str(clip.resolve()), cwd=work)
        dump(output / "manifest.json", {"metadata": episode.metadata.model_dump(), "duration": total,
             "file": clip.name, "original_production": True, "synthetic_narration": True,
             "sources": episode.sources, "checked_on": episode.checked_on, "chapters": chapters,
             "review_required": False})
        logger.info("Finished {} ({:.1f}s)", clip, total)
        return clip
    finally:
        # Only this renderer's isolated temporary directory; never user source media.
        if work.resolve().parent == output.resolve():
            shutil.rmtree(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(render(args.plan, args.output))


if __name__ == "__main__":
    main()
