import asyncio
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from loguru import logger

from .config import Account, Settings, Source, load_items
from .curator import curate
from .downloader import canonical_video, discover, download
from .editor import render
from .metadata import generate
from .publisher import Publisher
from .scheduler import Queue
from .transcriber import transcribe


@contextmanager
def worker_lock(folder: Path):
    """One pipeline/daemon per data directory, released automatically on crash."""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "worker.lock").open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another worker owns this data directory") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Pipeline:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.root = cfg.data_dir.resolve()
        self.stage = self.root / "staging"
        self.stage.mkdir(parents=True, exist_ok=True)
        self.queue = Queue(self.root / "queue.sqlite3")
        accounts = load_items(cfg.accounts_file, Account)
        if len({a.id for a in accounts}) != len(accounts):
            raise ValueError("Account ids must be unique")
        self.accounts = {a.id: a for a in accounts}

    async def process(self, url: str, account_ids: list[str], proxy: str | None = None):
        url = canonical_video(url)
        for account_id in account_ids:
            if account_id not in self.accounts:
                raise ValueError(f"Unknown account: {account_id}")
        key = hashlib.sha256(url.encode()).hexdigest()[:20]
        manifest = self.stage / f"{key}.json"
        if manifest.exists():
            items = json.loads(manifest.read_text(encoding="utf-8"))["clips"]
            if any(not (self.stage / item["file"]).exists() for item in items):
                raise RuntimeError("Manifest media is missing; restore it before retrying")
        else:
            with TemporaryDirectory(prefix="episode-", dir=self.root) as folder:
                temp = Path(folder)
                logger.info("Downloading audio for episode {}", key)
                audio = await download(url, temp, audio=True, proxy=proxy)
                words = await transcribe(audio, temp, self.cfg)
                moments = await curate(words, self.cfg)
                audio.unlink(missing_ok=True)
                items = []
                if moments:
                    media = await download(url, temp, proxy=proxy)
                    for i, moment in enumerate(moments):
                        metadata = await generate(moment, words, self.cfg)
                        name = f"{key}-{i:02d}.mp4"
                        destination = self.stage / name
                        await render(media, moment, words, temp / name, self.cfg)
                        (temp / name).replace(destination)
                        items.append({"file": name, "moment": moment.model_dump(),
                                      "metadata": metadata.model_dump()})
                payload = {"source": url, "clips": items, "transcript": [w.model_dump() for w in words]}
                pending = manifest.with_suffix(".tmp")
                pending.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                pending.replace(manifest)
        jobs = []
        for item in items:
            for account_id in account_ids:
                jobs.append(self.queue.enqueue(self.accounts[account_id], self.stage / item["file"],
                                               item["metadata"], self.cfg.review))
        logger.info("Staged {} clips; queue jobs {}", len(items), jobs)
        return manifest

    async def publish_tick(self):
        self.scan_outbox()
        for row in self.queue.rows():
            if row["state"] != "processing" or row["account"] not in self.accounts:
                continue
            # Instagram poll can perform a non-idempotent media_publish.
            account = self.accounts[row["account"]]
            if account.platform == "instagram":
                self.queue.set_state(row["id"], "uncertain")
            try:
                state, remote_id = await Publisher(account).poll(row["remote_id"])
                self.queue.set_state(row["id"], state, remote_id)
            except Exception as exc:
                logger.warning("Status check failed for job {}: {}", row["id"], type(exc).__name__)
        eligible = dict(self.accounts)
        pending_accounts = {r["account"] for r in self.queue.rows() if r["state"] == "queued"}
        # Auth failure occurs before claiming: leave jobs queued, not uncertain.
        for account in self.accounts.values():
            if account.id in pending_accounts and account.platform == "youtube" and account.refresh_token_env:
                try:
                    publisher = Publisher(account)
                    async with publisher.client() as client:
                        await publisher.authenticate(client)
                except Exception as exc:
                    eligible.pop(account.id, None)
                    logger.warning("YouTube account {} awaiting OAuth ({})", account.id, type(exc).__name__)
        row = self.queue.claim(eligible)
        if row:
            try:
                state, remote_id = await Publisher(self.accounts[row["account"]]).publish(
                    Path(row["clip"]), json.loads(row["metadata"]))
                self.queue.set_state(row["id"], state, remote_id)
            except Exception as exc:
                self.queue.set_state(row["id"], "uncertain", error=type(exc).__name__)
                logger.error("Upload job {} requires reconciliation ({})", row["id"], type(exc).__name__)

    def scan_outbox(self):
        if self.cfg.outbox_dir is None:
            return
        account = self.accounts.get(self.cfg.outbox_account)
        if account is None or account.platform != "youtube":
            raise ValueError("Outbox requires a configured YouTube account")
        root = self.cfg.outbox_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        for manifest in sorted(root.glob("*.json")):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                if payload.get("published") or payload.get("remote_id"):
                    continue
                clip = (root / payload["file"]).resolve()
                if not clip.is_relative_to(root) or clip.suffix.lower() != ".mp4":
                    raise ValueError("Invalid outbox media path")
                if not clip.is_file() or clip.stat().st_size == 0:
                    continue
                from .models import Metadata
                metadata = Metadata.model_validate(payload["metadata"]).model_dump()
                self.queue.enqueue(account, clip, metadata, review=False)
            except Exception as exc:
                logger.warning("Outbox manifest {} skipped ({})", manifest.name, type(exc).__name__)

    async def daemon(self):
        sources = load_items(self.cfg.sources_file, Source)
        self.queue.recover()

        async def publishing():
            while True:
                await self.publish_tick()
                await asyncio.sleep(30)

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(publishing())
            tasks.create_task(self.monitor(sources))

    async def monitor(self, sources: list[Source]):
        next_scan = 0.0
        while True:
            if time.monotonic() >= next_scan:
                for source in sources:
                    try:
                        urls = await discover(source)
                        proxy = os.environ.get(source.proxy_env) if source.proxy_env else None
                        if source.proxy_env and not proxy:
                            raise ValueError("Source proxy is not configured")
                        for url in reversed(urls):
                            url = canonical_video(url)
                            if not self.queue.episode_claim(url):
                                continue
                            try:
                                await self.process(url, source.accounts, proxy)
                                self.queue.episode_finish(url, True)
                            except Exception as exc:
                                self.queue.episode_finish(url, False)
                                logger.error("Episode processing failed: {}", type(exc).__name__)
                    except Exception as exc:
                        logger.error("Source scan failed: {}", type(exc).__name__)
                await self.manager_production_tick()
                next_scan = time.monotonic() + self.cfg.poll_seconds
            await asyncio.sleep(30)

    async def manager_production_tick(self):
        """Consume one approved editorial ticket using this daemon's existing lock."""
        from .manager_config import ApprovedSource
        for ticket in sorted((self.root / "manager" / "production").glob("*.json")):
            payload = None
            try:
                payload = json.loads(ticket.read_text(encoding="utf-8"))
                if payload.get("state") != "pending":
                    continue
                source = ApprovedSource.model_validate(payload["source"])
                account = self.accounts[payload["account"]]
                if not source.approved or account.platform != "youtube":
                    raise ValueError("Production ticket requires approved YouTube source")
                proxy = os.environ.get(account.proxy_env) if account.proxy_env else None
                if account.proxy_env and not proxy:
                    raise ValueError("Configured account proxy is missing")
                payload["state"] = "processing"
                self.write_ticket(ticket, payload)
                if not self.queue.episode_claim(source.url):
                    payload["state"] = "already_processed"
                else:
                    try:
                        agent = Pipeline(self.cfg.model_copy(update={"review": payload.get("review", True), "max_clips": 1}))
                        manifest = await agent.process(source.url, [], proxy=proxy)
                        media = json.loads(manifest.read_text(encoding="utf-8"))
                        for clip in media["clips"]:
                            if source.attribution:
                                clip["metadata"]["description"] = clip["metadata"]["description"][:900]+"\n"+source.attribution
                            self.queue.enqueue(account, self.stage / clip["file"], clip["metadata"], payload.get("review", True))
                        self.write_ticket(manifest, media)
                        payload["manifest"] = str(manifest)
                        self.queue.episode_finish(source.url, True)
                        payload["state"] = "staged"
                    except Exception:
                        self.queue.episode_finish(source.url, False)
                        raise
                self.write_ticket(ticket, payload)
                break
            except Exception as exc:
                logger.error("Manager production failed ({})", type(exc).__name__)
                if isinstance(payload, dict):
                    payload.update(state="failed", error=type(exc).__name__)
                    self.write_ticket(ticket, payload)

    @staticmethod
    def write_ticket(path, payload):
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        pending.replace(path)
