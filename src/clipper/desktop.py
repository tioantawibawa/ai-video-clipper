"""Laptop-only processing and explicit private uploads. No SSH or VPS dependency."""
import argparse
import asyncio
import json
import os
import shutil
import time
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel

from .config import Account, Settings, load_items
from .local import fingerprint, write_json
from .models import Metadata
from .pipeline import Pipeline, worker_lock
from .publisher import Publisher
from .scheduler import Queue


class DesktopConfig(BaseModel):
    env_file: Path = Path("credentials/desktop/youtube.env")
    llm_env_file: Path = Path("credentials/local-worker.env")
    data_dir: Path = Path("data/desktop")
    accounts_file: Path = Path("data/desktop/accounts.json")
    sources_file: Path = Path("data/desktop/sources.json")
    expected_channel: str = ""
    upload_backend: str = "native"
    autopilot_root: Path = Path(r"D:\youtube short")
    auto_upload_candidates: bool = False
    uploads_enabled: bool = True

    def load(self):
        load_dotenv(self.llm_env_file, override=False)
        load_dotenv(self.env_file, override=True)

    def settings(self):
        self.load()
        return Settings(data_dir=self.data_dir, accounts_file=self.accounts_file,
                        sources_file=self.sources_file, whisper_model="small",
                        outbox_dir=None, outbox_account="", review=True,
                        manager_render=False)

    def account(self):
        self.load()
        accounts = load_items(self.accounts_file, Account)
        if len(accounts) != 1 or accounts[0].platform != "youtube":
            raise ValueError("Desktop configuration requires exactly one YouTube account")
        if accounts[0].privacy != "private":
            raise ValueError("Desktop upload account must be private")
        return accounts[0]


async def connection(cfg, publisher=None):
    if cfg.upload_backend == "autopilot":
        from .autopilot_uploader import AutopilotUploader
        return await AutopilotUploader(cfg).call("preflight")
    if cfg.upload_backend != "native":
        raise ValueError("Unknown upload backend")
    publisher = publisher or Publisher(cfg.account())
    async with publisher.client() as client:
        await publisher.authenticate(client)
        r = await client.get("https://www.googleapis.com/youtube/v3/channels",
                             params={"part": "snippet", "mine": "true"})
        r.raise_for_status()
        channels = r.json().get("items", [])
        if len(channels) != 1:
            raise ValueError("Select exactly one YouTube channel when authorizing")
        channel = channels[0]
        if cfg.expected_channel and channel["id"] != cfg.expected_channel:
            raise ValueError("OAuth belongs to a different YouTube channel")
        return {"channel_id": channel["id"], "title": channel["snippet"]["title"]}


async def upload_private(cfg, clip, manifest, poll_attempts=40):
    """Authenticate before reserving, record unknown outcomes, deduplicate by content."""
    if not cfg.uploads_enabled:
        raise ValueError("Uploads paused pending channel selection")
    cfg.load()
    account = cfg.account()
    clip, manifest = clip.resolve(), manifest.resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not clip.is_file() or clip.stat().st_size == 0:
        raise ValueError("A nonempty clip is required")
    digest = await asyncio.to_thread(fingerprint, clip)
    if payload.get("clip_sha256") and payload["clip_sha256"] != digest:
        raise ValueError("Clip changed since review; checksum mismatch")
    metadata = Metadata.model_validate(payload["metadata"]).model_dump()
    # A metadata publish_at must never silently schedule a public release.
    if payload["metadata"].get("publish_at"):
        raise ValueError("Scheduled public release is not supported by private upload")
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with worker_lock(cfg.data_dir):
        queue = Queue(cfg.data_dir / "queue.sqlite3")
        if cfg.upload_backend == "autopilot":
            from .autopilot_uploader import AutopilotUploader
            publisher = AutopilotUploader(cfg)
            publisher.source = payload.get("source", "")
        else:
            publisher = Publisher(account)
        # No new job is created if OAuth is broken or the wrong channel is selected.
        channel = await connection(cfg, publisher)
        canonical = (cfg.data_dir / "uploads" / (digest + ".mp4")).resolve()
        with queue.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE account=? AND clip=?",
                             (account.id, str(canonical))).fetchone()
            if row:
                job, remote_id = row["id"], row["remote_id"]
                if not remote_id and cfg.upload_backend == "autopilot":
                    saved = publisher.saved(canonical)
                    if saved.get("clip_sha256") == digest:
                        remote_id = saved.get("remote_id")
                if not remote_id:
                    raise ValueError("Uncertain upload exists; reconcile it before any retry")
            else:
                if payload.get("remote_id") or payload.get("published") or payload.get("uploaded"):
                    raise ValueError("Manifest already references an upload; reconcile instead")
                # Import history without importing old publication schedules.
                history = cfg.data_dir / "migration-history.json"
                if history.exists():
                    for old in json.loads(history.read_text()).get("jobs", []):
                        if old.get("clip_sha256") == digest:
                            raise ValueError("Clip exists in former VPS history; reconcile instead")
                canonical.parent.mkdir(parents=True, exist_ok=True)
                if not canonical.exists():
                    pending = canonical.with_suffix(".tmp")
                    await asyncio.to_thread(shutil.copyfile, clip, pending)
                    pending.replace(canonical)
                if await asyncio.to_thread(fingerprint, canonical) != digest:
                    raise ValueError("Stored upload copy failed checksum validation")
                now = time.time()
                job = db.execute("INSERT INTO jobs(account,clip,metadata,state,due,updated) "
                                 "VALUES(?,?,?,?,?,?)", (account.id, str(canonical),
                                 json.dumps(metadata), "uncertain", now, now)).lastrowid
                remote_id = None
        if not remote_id:
            # A crash or timeout leaves the durable uncertain reservation intact.
            _, remote_id = await publisher.publish(canonical, metadata)
            queue.set_state(job, "held", remote_id)
            payload.update(job_id=job, remote_id=remote_id, privacy="private", uploaded=True,
                           published=False, state="held", clip_sha256=digest)
            write_json(manifest, payload)
        for attempt in range(poll_attempts):
            if cfg.upload_backend == "autopilot":
                result = await publisher.status(canonical)
                if result.get("privacy") != "private" or (cfg.expected_channel and
                        result.get("channel_id") != cfg.expected_channel):
                    raise ValueError("Upload channel/privacy mismatch; ID retained")
                if result.get("processing") in {"failed", "terminated"}:
                    queue.set_state(job, "failed", remote_id)
                    raise ValueError("YouTube processing failed; ID retained")
                if result.get("processing") in {"succeeded", "unverified"}:
                    result["job_id"] = job
                    queue.set_state(job, "held", remote_id)
                    payload.update(job_id=job, remote_id=remote_id, privacy="private", uploaded=True,
                                   published=False, state="held", clip_sha256=digest)
                    write_json(manifest, payload)
                    receipt = cfg.data_dir / "receipts" / (digest + ".json")
                    receipt.parent.mkdir(parents=True, exist_ok=True)
                    write_json(receipt, result)
                    return result
                if attempt + 1 < poll_attempts:
                    await asyncio.sleep(3)
                continue
            async with publisher.client() as client:
                await publisher.authenticate(client)
                r = await client.get("https://www.googleapis.com/youtube/v3/videos", params={
                    "part": "status,processingDetails,snippet", "id": remote_id})
                r.raise_for_status()
                items = r.json().get("items", [])
            if items:
                video = items[0]
                status = video["status"]
                if status.get("privacyStatus") != "private" or status.get("publishAt"):
                    raise ValueError("Unexpected privacy or scheduled release; inspect YouTube Studio")
                processing = video.get("processingDetails", {}).get("processingStatus")
                if processing in {"failed", "terminated"}:
                    queue.set_state(job, "failed", remote_id)
                    raise ValueError("YouTube processing failed; remote ID retained")
                if processing == "succeeded":
                    result = {"job_id": job, "remote_id": remote_id, "privacy": "private",
                              "processing": processing, "title": video["snippet"]["title"],
                              "url": "https://www.youtube.com/watch?v=" + remote_id, **channel}
                    payload.update(job_id=job, remote_id=remote_id, privacy="private", uploaded=True,
                                   published=False, state="held", clip_sha256=digest)
                    write_json(manifest, payload)
                    receipt = cfg.data_dir / "receipts" / (digest + ".json")
                    receipt.parent.mkdir(parents=True, exist_ok=True)
                    write_json(receipt, result)
                    return result
            if attempt + 1 < poll_attempts:
                await asyncio.sleep(3)
        return {"job_id": job, "remote_id": remote_id, "privacy": "private",
                "processing": "pending", "url": "https://www.youtube.com/watch?v=" + remote_id}


def read_config(path):
    return DesktopConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("data/desktop/config.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--live", action="store_true")
    process = commands.add_parser("process", help="Download and edit locally; stage for review only")
    process.add_argument("url")
    upload = commands.add_parser("upload", help="Explicit private upload directly from this computer")
    upload.add_argument("clip", type=Path)
    upload.add_argument("manifest", type=Path)
    submit = commands.add_parser("submit", help="Queue a finished candidate for private upload")
    submit.add_argument("clip", type=Path)
    submit.add_argument("manifest", type=Path)
    watch = commands.add_parser("watch", help="Upload explicitly queued local candidates")
    watch.add_argument("--once", action="store_true")
    commands.add_parser("queue")
    args = parser.parse_args()
    cfg = read_config(args.config)
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), backtrace=False, diagnose=False)
    try:
        if args.command == "doctor":
            account = cfg.account()
            result = {"execution": "local", "privacy": account.privacy,
                      "upload_backend": cfg.upload_backend,
                      "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
                      "oauth_configured": ((cfg.autopilot_root / "config/token.json").is_file()
                          if cfg.upload_backend == "autopilot" else all(os.getenv(ref or "") for ref in (
                          account.client_id_env, account.client_secret_env, account.refresh_token_env)))}
            if args.live:
                result.update(asyncio.run(connection(cfg)))
            print(json.dumps(result))
        elif args.command == "upload":
            print(json.dumps(asyncio.run(upload_private(cfg, args.clip, args.manifest))))
        elif args.command == "submit":
            from .autopilot_uploader import submit_candidate
            print(submit_candidate(cfg, args.clip, args.manifest))
        elif args.command == "watch":
            from .autopilot_uploader import watch
            asyncio.run(watch(cfg, once=args.once))
        elif args.command == "process":
            agent = Pipeline(cfg.settings())
            with worker_lock(cfg.data_dir):
                bundle = asyncio.run(agent.process(args.url, []))
            if cfg.auto_upload_candidates:
                from .autopilot_uploader import submit_bundle, watch
                submit_bundle(cfg, bundle)
                asyncio.run(watch(cfg, once=True))
            print(bundle)
        else:
            print(json.dumps([dict(r) for r in Queue(cfg.data_dir / "queue.sqlite3").rows()]))
    except Exception as exc:
        # HTTP exception messages and response bodies may contain OAuth secrets.
        logger.error("Desktop operation failed ({}); inspect configuration and saved receipts", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
