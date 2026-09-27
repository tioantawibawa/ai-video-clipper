import asyncio
from pathlib import Path


async def run(*args: str, cwd: Path | None = None, timeout: int = 7200) -> str:
    """Never invoke a shell. Kill and reap children on timeout/cancellation."""
    proc = await asyncio.create_subprocess_exec(
        *map(str, args), cwd=cwd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    text = output.decode(errors="replace")
    if proc.returncode:
        # yt-dlp errors may contain authenticated URLs; do not expose command/output.
        detail = text[-4000:] if Path(args[0]).name in {"ffmpeg", "ffprobe"} else ""
        raise RuntimeError(f"{Path(args[0]).name} exited with code {proc.returncode}: {detail}")
    return text
