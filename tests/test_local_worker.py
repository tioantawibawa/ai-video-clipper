import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from clipper import local_worker as worker


def config(tmp_path):
    return worker.WorkerConfig(inbox=tmp_path / "inbox", output=tmp_path / "output",
        env_file=tmp_path / "env", ssh_key=tmp_path / "key", known_hosts=tmp_path / "hosts",
        host="ubuntu@43.156.75.116", remote_outbox="/home/ubuntu/ai-video-clipper/data/youtube-outbox")


def test_local_media_must_stay_inside_inbox(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        worker.inside(tmp_path / "inbox", "../private.mp4")


def test_local_job_delivered_once(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    cfg.inbox.mkdir()
    request = cfg.inbox / "first.request.json"
    request.write_text(json.dumps({"media": "source.mp4", "source": "https://youtube.com/watch?v=abc",
        "rights_note": "Creative Commons approved source", "approved": True, "credit": "Creator"}))
    async def edit(args):
        args.output.mkdir()
        clip = args.output / "clip.mp4"
        clip.write_bytes(b"rendered")
        return clip
    monkeypatch.setattr(worker.local, "process", AsyncMock(side_effect=edit))
    sender = AsyncMock(return_value="laptop-id")
    monkeypatch.setattr(worker, "deliver", sender)
    assert asyncio.run(worker.tick(cfg))[0]["state"] == "submitted"
    assert asyncio.run(worker.tick(cfg)) == []
    sender.assert_awaited_once()


def test_uncertain_transfer_is_not_retried(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    cfg.inbox.mkdir()
    (cfg.inbox / "first.request.json").write_text(json.dumps({"media": "source.mp4",
        "source": "original", "rights_note": "Original content approved", "approved": True, "credit": "Owner"}))
    monkeypatch.setattr(worker.local, "process", AsyncMock(return_value=tmp_path / "clip.mp4"))
    sender = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(worker, "deliver", sender)
    assert asyncio.run(worker.tick(cfg))[0]["state"] == "failed"
    assert asyncio.run(worker.tick(cfg)) == []
    sender.assert_awaited_once()


def test_transfer_checks_hash_and_promotes_manifest_last(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    cfg.ssh_key.write_text("test key")
    cfg.known_hosts.write_text("test host")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"metadata": {"title": "Test", "description": "", "hashtags": []},
                                   "published": False}))
    run = AsyncMock(return_value="")
    monkeypatch.setattr(worker, "run", run)
    result = asyncio.run(worker.deliver(cfg, clip, manifest))
    assert result.startswith("laptop-")
    command = run.await_args_list[-1].args[-1]
    assert "sha256sum -c" in command
    assert command.index(".mp4.part") < command.index(".json.part")
    assert "StrictHostKeyChecking=yes" in run.await_args_list[0].args


def test_sync_copies_only_whitelisted_llm_settings(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    monkeypatch.setattr(worker, "run", AsyncMock(return_value=json.dumps({
        "CLIPPER_LLM_MODEL": "openrouter/free", "CLIPPER_OPENROUTER_API_KEY": "test-key"})))
    asyncio.run(worker.sync_env(cfg))
    assert "YOUTUBE" not in cfg.env_file.read_text()
    assert "openrouter/free" in cfg.env_file.read_text()


def test_sync_rejects_account_credentials(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    monkeypatch.setattr(worker, "run", AsyncMock(return_value='{"YOUTUBE_REFRESH_TOKEN":"test"}'))
    with pytest.raises(ValueError, match="Unexpected"):
        asyncio.run(worker.sync_env(cfg))
    assert not cfg.env_file.exists()
