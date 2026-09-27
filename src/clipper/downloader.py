import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import feedparser
import httpx

from .config import Source
from .process import run


def youtube_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"
    } or parsed.username or parsed.password:
        raise ValueError("Expected an HTTPS YouTube URL")
    return url


def canonical_video(url: str) -> str:
    parsed = urlparse(youtube_url(url))
    if parsed.hostname == "youtu.be":
        video_id = parsed.path.strip("/")
    elif parsed.path.startswith(("/shorts/", "/live/")):
        video_id = parsed.path.split("/")[2]
    else:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    if not video_id or not all(c.isalnum() or c in "_-" for c in video_id):
        raise ValueError("Expected an individual YouTube video URL")
    return f"https://www.youtube.com/watch?v={video_id}"


async def download(url: str, folder: Path, audio: bool = False, proxy: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-progress",
            "--socket-timeout", "30", "--retries", "3", "--max-filesize", "4G",
            "-o", str(folder / ("audio.%(ext)s" if audio else "source.%(ext)s"))]
    if proxy:
        args += ["--proxy", proxy]
    args += (["-f", "ba[abr<=96]/ba/best"] if audio else
             ["-f", "bv*[height<=720]+ba/b[height<=720]", "--merge-output-format", "mp4"])
    await run(*args, "--", youtube_url(url))
    prefix = "audio" if audio else "source"
    files = [p for p in folder.glob(f"{prefix}.*") if p.suffix not in {".part", ".ytdl"}]
    if len(files) != 1:
        raise RuntimeError("Download did not produce exactly one media file")
    return files[0]


async def discover(source: Source) -> list[str]:
    proxy = os.environ.get(source.proxy_env) if source.proxy_env else None
    if source.proxy_env and not proxy:
        raise ValueError("Source proxy is not configured")
    if source.kind == "rss":
        if not source.url.startswith("https://"):
            raise ValueError("RSS requires HTTPS")
        async with httpx.AsyncClient(proxy=proxy, timeout=30) as client:
            response = await client.get(source.url)
            response.raise_for_status()
        feed = await asyncio.to_thread(feedparser.parse, response.text)
        return [youtube_url(e.link) for e in feed.entries[:source.limit]]
    args = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--dump-single-json",
            "--playlist-end", str(source.limit), "--socket-timeout", "30"]
    if proxy:
        args += ["--proxy", proxy]
    data = json.loads(await run(*args, "--", youtube_url(source.url)))
    return [f"https://www.youtube.com/watch?v={e['id']}" for e in data.get("entries", [])]
