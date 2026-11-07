import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper import autopilot_uploader as uploader
from clipper import desktop
from clipper.local import fingerprint
from clipper.scheduler import Queue


def fixture(tmp_path):
    cfg = desktop.DesktopConfig(data_dir=tmp_path / "data", accounts_file=tmp_path / "accounts.json",
        env_file=tmp_path / "env", llm_env_file=tmp_path / "llm", upload_backend="autopilot",
        expected_channel="ours")
    cfg.accounts_file.write_text(json.dumps([{"id": "yt", "platform": "youtube", "token_env": "T", "privacy": "private"}]))
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"finished")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"metadata": {"title": "Clip", "description": "Original", "hashtags": []}}))
    return cfg, clip, manifest


def test_queue_idempotent_and_only_explicit_candidates(tmp_path, monkeypatch):
    cfg, clip, manifest = fixture(tmp_path)
    old = tmp_path / "old.mp4"
    old.write_bytes(b"historical")
    ticket = uploader.submit_candidate(cfg, clip, manifest)
    calls = []

    async def upload(*args):
        calls.append(args)
        return {"remote_id": "private123", "processing": "unverified"}

    monkeypatch.setattr(uploader, "checked_upload", upload)
    asyncio.run(uploader.watch(cfg, once=True))
    uploader.submit_candidate(cfg, clip, manifest)
    asyncio.run(uploader.watch(cfg, once=True))
    assert len(calls) == 1
    assert json.loads(ticket.read_text())["state"] == "uploaded"


def test_ambiguous_upload_is_not_retried(tmp_path, monkeypatch):
    cfg, clip, manifest = fixture(tmp_path)
    ticket = uploader.submit_candidate(cfg, clip, manifest)
    calls = []

    async def upload(*args):
        calls.append(args)
        raise TimeoutError()

    monkeypatch.setattr(uploader, "checked_upload", upload)
    asyncio.run(uploader.watch(cfg, once=True))
    asyncio.run(uploader.watch(cfg, once=True))
    assert len(calls) == 1
    assert json.loads(ticket.read_text())["state"] == "needs_attention"


def test_autopilot_private_receipt_and_dedup(tmp_path, monkeypatch):
    cfg, clip, manifest = fixture(tmp_path)
    calls = []

    async def call(self, operation, *args):
        if operation == "preflight":
            return {"connected": True, "identity_verified": False}
        if operation == "upload":
            calls.append(operation)
        return {"remote_id": "private123", "privacy": "private", "channel_id": "ours",
                "processing": "unverified", "verification": "insert_response"}

    monkeypatch.setattr(uploader.AutopilotUploader, "call", call)
    first = asyncio.run(desktop.upload_private(cfg, clip, manifest))
    second = asyncio.run(desktop.upload_private(cfg, clip, manifest))
    assert first == second
    assert first["processing"] == "unverified"
    assert len(calls) == 1


def test_recover_persisted_id_without_second_upload(tmp_path, monkeypatch):
    cfg, clip, manifest = fixture(tmp_path)
    digest = fingerprint(clip)
    canonical = (cfg.data_dir / "uploads" / (digest + ".mp4")).resolve()
    queue = Queue(cfg.data_dir / "queue.sqlite3")
    with queue.connect() as db:
        db.execute("INSERT INTO jobs(account,clip,metadata,state,due,updated) VALUES(?,?,?,?,?,?)",
                   ("yt", str(canonical), "{}", "uncertain", 0, 0))
    adapter = uploader.AutopilotUploader(cfg)
    receipt = adapter.receipt_path(canonical)
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"remote_id": "saved123", "clip_sha256": digest}))

    async def call(self, operation, *args):
        assert operation != "upload"
        return {"remote_id": "saved123", "privacy": "private", "channel_id": "ours", "processing": "unverified"}

    monkeypatch.setattr(uploader.AutopilotUploader, "call", call)
    result = asyncio.run(desktop.upload_private(cfg, clip, manifest))
    assert result["remote_id"] == "saved123"
    assert queue.rows()[0]["state"] == "held"


def test_read_scope_failure_does_not_claim_processing_success():
    spec = importlib.util.spec_from_file_location("bridge", Path(__file__).parents[1] / "scripts/autopilot_bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    class Denied(Exception):
        resp = SimpleNamespace(status=403)

    class Youtube:
        def videos(self):
            return self

        def list(self, **kwargs):
            return self

        def execute(self, **kwargs):
            raise Denied()

    receipt = {"remote_id": "private123", "privacy": "private", "channel_id": "ours"}
    assert bridge.verify(Youtube(), "private123", "ours", receipt)["processing"] == "unverified"
    with pytest.raises(ValueError, match="mismatch"):
        bridge.verify(Youtube(), "private123", "wrong", receipt)


def test_original_upload_is_private_and_id_survives_channel_mismatch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bridge", Path(__file__).parents[1] / "scripts/autopilot_bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "step4_uploader.py").touch()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"metadata": {"title": "Clip", "description": "Original #Shorts", "hashtags": ["#Shorts"]}}))
    receipt = tmp_path / "receipt.json"
    calls = []

    def original_upload(path, title, description, **kwargs):
        calls.append(kwargs)
        assert description.count("#Shorts") == 1
        return {"youtube_id": "accepted123", "raw_response": {"status": {"privacyStatus": "private"},
                "snippet": {"channelId": "different-channel"}}}

    module = SimpleNamespace(upload_short=original_upload)
    monkeypatch.setattr(bridge, "service", lambda root: (module, None))
    monkeypatch.setattr(bridge, "inspect_channel", lambda *args: {"can_read": False})
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace(root=tmp_path, operation="upload", expected_channel="ours", receipt=receipt,
                           clip=clip, manifest=manifest)
    with pytest.raises(ValueError, match="mismatch"):
        bridge.execute(args)
    assert calls[0]["privacy_status"] == "private"
    assert json.loads(receipt.read_text())["remote_id"] == "accepted123"


def test_bundle_does_not_escape_staging(tmp_path):
    cfg, clip, manifest = fixture(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    bundle = staging / "bundle.json"
    bundle.write_text(json.dumps({"clips": [{"file": "../clip.mp4", "metadata": {}}]}))
    with pytest.raises(ValueError, match="inside staging"):
        uploader.submit_bundle(cfg, bundle)


def test_paused_upload_does_not_reserve_or_contact_youtube(tmp_path):
    cfg, clip, manifest = fixture(tmp_path)
    cfg.uploads_enabled = False
    with pytest.raises(ValueError, match="paused"):
        asyncio.run(desktop.upload_private(cfg, clip, manifest))
    assert not (cfg.data_dir / "queue.sqlite3").exists()
