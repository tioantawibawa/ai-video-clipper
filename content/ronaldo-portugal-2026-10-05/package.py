"""Generate original graphic thumbnails and chaptered descriptions after rendering."""
import json
from pathlib import Path
from PIL import Image, ImageDraw
from clipper.explainer import font

ROOT = Path("data/original-ronaldo")
ROOT.mkdir(parents=True, exist_ok=True)
for index, lines in enumerate([("NOT", "OVER?"), ("START OR", "BENCH?"), ("WHAT", "CHANGED?")], 1):
    image = Image.new("RGB", (1280, 720), "#0b1422")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0,0,1280,12), fill="#e43745")
    draw.text((55,48), "RONALDO / PORTUGAL", font=font(37, True), fill="white")
    for row, text in enumerate(lines):
        draw.text((50,190+row*145), text, font=font(116, True), fill="#f4c64b")
    # An original jersey symbol avoids presenting fabricated photos as news evidence.
    draw.polygon([(870,125),(965,160),(1030,125),(1190,220),(1130,325),(1060,290),
                  (1060,605),(815,605),(815,290),(750,325),(695,220)], fill="#dd3346")
    draw.line((850,143,870,185,963,185,982,143), fill="white", width=8)
    draw.text((864,280), "7", font=font(230,True), fill="white")
    draw.text((57,642), "ORIGINAL FOOTBALL ANALYSIS", font=font(27,True), fill="#9eafc1")
    image.save(ROOT / f"thumbnail-{index}.jpg", quality=94)

manifest = ROOT / "main/manifest.json"
payload = json.loads(manifest.read_text())
chapters = []
for chapter in payload["chapters"]:
    seconds = round(chapter["start"])
    chapters.append(f"{seconds//60}:{seconds%60:02} {chapter['heading']}")
description = payload["metadata"]["description"] + "\n\nChapters:\n" + "\n".join(chapters)
assert len(description) <= 2000
payload["metadata"]["description"] = description
manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
(ROOT / "publication-plan.json").write_text(json.dumps({
    "order": ["main", "short-1", "short-2", "short-3"],
    "daily_public_limit": 1, "timezone": "America/New_York",
    "related_video": "Set all Shorts to main video after main becomes public",
    "ab_testing": "Three packaging variants prepared; Studio experiment not yet active",
}, indent=2), encoding="utf-8")
print("Thumbnails and chaptered main description prepared")
