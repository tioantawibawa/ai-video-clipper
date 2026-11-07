"""Adapter to the user's existing laptop uploader, using its venv and token file."""
import asyncio
import json
from pathlib import Path

from .local import write_json
from .process import run


def submit_candidate(cfg, clip, manifest):
    from .local import fingerprint
    from .models import Metadata
    clip, manifest = clip.resolve(), manifest.resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    Metadata.model_validate(payload["metadata"])
    if not clip.is_file() or not clip.stat().st_size:
        raise ValueError("Nonempty finished clip required")
    digest = fingerprint(clip)
    if payload.get("clip_sha256") and payload["clip_sha256"] != digest:
        raise ValueError("Checksum mismatch")
    root = cfg.data_dir / "candidates"
    root.mkdir(parents=True, exist_ok=True)
    ticket = root / (digest + ".json")
    if not ticket.exists():
        write_json(ticket, {"clip": str(clip), "manifest": str(manifest),
                            "clip_sha256": digest, "state": "ready"})
    return ticket


def submit_bundle(cfg, bundle):
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    tickets = []
    for item in payload["clips"]:
        clip = (bundle.parent / item["file"]).resolve()
        if not clip.is_relative_to(bundle.parent.resolve()):
            raise ValueError("Candidate must stay inside staging")
        manifest = clip.with_suffix(".candidate.json")
        if not manifest.exists():
            write_json(manifest, {"source": payload.get("source"), "metadata": item["metadata"],
                                  "moment": item.get("moment"), "review_required": True})
        tickets.append(submit_candidate(cfg, clip, manifest))
    return tickets


class AutopilotUploader:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = cfg.autopilot_root.resolve()
        self.python = self.root / "venv" / "Scripts" / "python.exe"
        self.bridge = Path(__file__).resolve().parents[2] / "scripts" / "autopilot_bridge.py"

    async def call(self, operation, *arguments):
        if not self.python.is_file():
            raise ValueError("Existing autopilot Python environment is unavailable")
        output = await run(str(self.python), str(self.bridge), "--root", str(self.root),
            "--operation", operation, "--expected-channel", self.cfg.expected_channel,
            *map(str, arguments), cwd=self.root, timeout=900)
        result = json.loads(output)
        if result.get("error"):
            raise ValueError("Existing uploader needs attention")
        return result

    def receipt_path(self, clip):
        return self.cfg.data_dir.resolve() / "bridge" / (clip.stem + ".json")

    def saved(self, clip):
        path = self.receipt_path(clip)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    async def publish(self, clip, metadata):
        payload = clip.with_suffix(".upload.json")
        write_json(payload, {"metadata": metadata, "source": getattr(self, "source", "")})
        try:
            result = await self.call("upload", "--clip", clip, "--manifest", payload,
                                     "--receipt", self.receipt_path(clip))
        except Exception:
            # The child may have persisted the remote ID before failing later.
            result = self.saved(clip)
            if not result.get("remote_id"):
                raise
        return "processing", result["remote_id"]

    async def status(self, clip):
        return await self.call("status", "--receipt", self.receipt_path(clip))


async def checked_upload(cfg, clip, manifest):
    # Keep the durable ledger and verification in one shared upload entrypoint.
    from .desktop import upload_private
    return await upload_private(cfg, clip, manifest)


async def watch(cfg, once=False):
    """Only consume explicit ready tickets; never scan or replay old staging folders."""
    from .pipeline import worker_lock
    root = cfg.data_dir / "candidates"
    root.mkdir(parents=True, exist_ok=True)
    with worker_lock(cfg.data_dir / "candidate-worker"):
        while True:
            for ticket in sorted(root.glob("*.json")):
                try:
                    payload = json.loads(ticket.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if payload.get("state") != "ready":
                    continue
                payload["state"] = "uploading"
                write_json(ticket, payload)
                print(json.dumps({"candidate": ticket.stem, "state": payload["state"],
                                  "result": payload.get("result"), "error": payload.get("error")}), flush=True)
                try:
                    result = await checked_upload(cfg, Path(payload["clip"]), Path(payload["manifest"]))
                    payload.update(state="uploaded", result=result)
                except Exception as exc:
                    # No automatic retry of an ambiguous upload or failed preflight.
                    payload.update(state="needs_attention", error=type(exc).__name__)
                write_json(ticket, payload)
                print(json.dumps({"candidate": ticket.stem, "state": payload["state"],
                                  "result": payload.get("result"), "error": payload.get("error")}), flush=True)
            if once:
                return
            await asyncio.sleep(60)
