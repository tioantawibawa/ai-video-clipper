"""Watch authorized local media requests, render locally, and deliver to a VPS outbox."""
import argparse
import asyncio
from datetime import date
import hashlib
import json
import shlex
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel, Field

from . import local
from .models import Metadata
from .pipeline import worker_lock
from .process import run


class WorkerConfig(BaseModel):
    inbox: Path
    output: Path
    env_file: Path
    ssh_key: Path
    known_hosts: Path
    host: str = Field(pattern=r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+$")
    remote_outbox: str = Field(pattern=r"^/[a-zA-Z0-9_/-]+$")
    poll_seconds: int = Field(60, ge=30)
    research_output: Path | None = None


class EditRequest(BaseModel):
    media: str
    source: str = Field(min_length=1)
    rights_note: str = Field(min_length=10)
    approved: bool = False
    credit: str = Field(min_length=1)
    start: float | None = None
    end: float | None = None
    title: str | None = None
    description: str = ""
    transcript: str | None = None


def inside(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Local request path escapes inbox")
    return path


def ssh_options(cfg):
    return ["-i", str(cfg.ssh_key.resolve()), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", 'UserKnownHostsFile="'+cfg.known_hosts.resolve().as_posix()+'"',
            "-o", "ConnectTimeout=15"]


async def sync_env(cfg):
    """Copy only LLM settings over pinned SSH; never expose values in logs."""
    names = ["CLIPPER_LLM_PROVIDER", "CLIPPER_LLM_MODEL", "CLIPPER_LLM_FALLBACK_ENABLED",
             "CLIPPER_OPENROUTER_API_KEY", "CLIPPER_GEMINI_API_KEY", "CLIPPER_GEMINI_FALLBACK_MODEL",
             "CLIPPER_OPENROUTER_PAID_MODEL", "CLIPPER_OPENAI_API_KEY", "CLIPPER_ANTHROPIC_API_KEY"]
    code = ("import json; from dotenv import dotenv_values; d=dotenv_values('.env'); "
            +"print(json.dumps({k:d[k] for k in "+repr(names)+" if d.get(k)}))")
    command = "cd /home/ubuntu/ai-video-clipper && .venv/bin/python -c "+shlex.quote(code)
    values = json.loads(await run("ssh", *ssh_options(cfg), cfg.host, command, timeout=60))
    if not isinstance(values, dict) or set(values)-set(names):
        raise ValueError("Unexpected remote environment settings")
    if not any(values.get(x) for x in names if x.endswith("API_KEY")):
        raise ValueError("No LLM credentials configured on VPS")
    values.update(CLIPPER_CPU_THREADS="4", CLIPPER_WHISPER_MODEL="small.en", CLIPPER_DEVICE="auto")
    cfg.env_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.env_file.write_text("\n".join(k+"="+json.dumps(v) for k, v in values.items())+"\n", encoding="utf-8")
    cfg.env_file.chmod(0o600)
    logger.info("Local LLM configuration synchronized; OAuth/account credentials were excluded")


async def sync_research(cfg):
    if cfg.research_output is None:
        return
    from .trend_research import TrendResearch
    report = json.loads(await run("ssh", *ssh_options(cfg), cfg.host,
        "cat /home/ubuntu/ai-video-clipper/data/vps/research/latest.json", timeout=60))
    date.fromisoformat(report.get("target_date", ""))
    cfg.research_output.mkdir(parents=True, exist_ok=True)
    local.write_json(cfg.research_output / "latest.json", report)
    TrendResearch.write_report(cfg.research_output / "latest.md", report)


async def deliver(cfg, clip, manifest):
    if not cfg.ssh_key.is_file() or not cfg.known_hosts.is_file():
        raise ValueError("VPS key and pinned host key are required")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("published") or payload.get("remote_id"):
        raise ValueError("Clip already has a publication receipt")
    Metadata.model_validate(payload["metadata"])
    digest = await asyncio.to_thread(local.fingerprint, clip)
    name = "laptop-"+digest
    # Fixed safe paths; manifest is promoted last so the VPS never queues a partial upload.
    root = cfg.remote_outbox
    options = ssh_options(cfg)
    await run("ssh", *options, cfg.host, "mkdir -p "+root, timeout=60)
    await run("scp", *options, str(clip), cfg.host+":"+root+"/"+name+".mp4.part", timeout=7200)
    payload.update(file=name+".mp4", review_required=False, clip_sha256=digest)
    transport = clip.parent / "outbox.json"
    local.write_json(transport, payload)
    await run("scp", *options, str(transport), cfg.host+":"+root+"/"+name+".json.part", timeout=120)
    command = ("cd "+root+" && printf '%s  %s\\n' "+digest+" "+name+".mp4.part | sha256sum -c - >/dev/null"
               +" && mv "+name+".mp4.part "+name+".mp4 && mv "+name+".json.part "+name+".json")
    await run("ssh", *options, cfg.host, command, timeout=120)
    return name


async def tick(cfg):
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    cfg.output.mkdir(parents=True, exist_ok=True)
    results = []
    for request in sorted(cfg.inbox.glob("*.request.json")):
        key = hashlib.sha256(request.name.encode()).hexdigest()[:24]
        output = cfg.output / key
        receipt = cfg.output / (key+".state.json")
        if receipt.exists():
            # Submitted, interrupted, or failed work requires deliberate reconciliation.
            continue
        state = {"request": request.name, "state": "processing"}
        local.write_json(receipt, state)
        try:
            job = EditRequest.model_validate_json(request.read_text(encoding="utf-8-sig"))
            if not job.approved:
                raise ValueError("Source reuse must be approved")
            argv = [str(inside(cfg.inbox, job.media)), "--output", str(output),
                    "--source", job.source, "--credit", job.credit, "--description", job.description]
            if job.start is not None or job.end is not None:
                if job.start is None or job.end is None or not job.title:
                    raise ValueError("Manual edits require start, end and title")
                argv += ["--start", str(job.start), "--end", str(job.end), "--title", job.title]
            if job.transcript:
                argv += ["--transcript", str(inside(cfg.inbox, job.transcript))]
            clip = await local.process(local.parser().parse_args(argv))
            state.update(state="rendered", clip=str(clip))
            local.write_json(receipt, state)
            remote = await deliver(cfg, clip, output / "manifest.json")
            state.update(state="submitted", outbox_id=remote)
            logger.info("Local clip delivered to VPS outbox: {}", remote)
        except Exception as exc:
            state.update(state="failed", error=type(exc).__name__)
            logger.error("Local request {} failed ({}); inspect receipt before retrying", request.name, type(exc).__name__)
        finally:
            local.write_json(receipt, state)
        results.append(state)
    return results


async def watch(cfg, once=False):
    load_dotenv(cfg.env_file)
    cfg.output.mkdir(parents=True, exist_ok=True)
    with worker_lock(cfg.output):
        next_research = 0
        while True:
            if cfg.research_output and time.monotonic() >= next_research:
                try:
                    await sync_research(cfg)
                except Exception as exc:
                    logger.warning("Research report sync unavailable ({})", type(exc).__name__)
                next_research = time.monotonic()+1800
            await tick(cfg)
            if once:
                return
            await asyncio.sleep(cfg.poll_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--sync-env", action="store_true", help="Copy LLM settings from the configured VPS")
    args = parser.parse_args()
    cfg = WorkerConfig.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
    logger.remove()
    cfg.output.mkdir(parents=True, exist_ok=True)
    logger.add(sys.stderr, backtrace=False, diagnose=False)
    logger.add(cfg.output / "worker.log", rotation="10 MB", retention="14 days", backtrace=False, diagnose=False)
    try:
        asyncio.run(sync_env(cfg) if args.sync_env else watch(cfg, args.once))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        logger.error("Local worker stopped ({})", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
