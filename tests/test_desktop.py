import asyncio
import json

import httpx
import pytest

from clipper import desktop
from clipper.scheduler import Queue
from clipper.youtube_auth import desktop_client, save_env


def setup(tmp_path, monkeypatch, *, auth_error=False, upload_error=False,
          privacy="private", channel="our-channel", processing="succeeded"):
    cfg = desktop.DesktopConfig(env_file=tmp_path / "env", llm_env_file=tmp_path / "llm-env",
        data_dir=tmp_path / "data", accounts_file=tmp_path / "accounts.json",
        expected_channel="our-channel")
    cfg.accounts_file.write_text(json.dumps([{"id": "yt", "platform": "youtube",
        "token_env": "T", "privacy": "private"}]))
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"reviewed-video")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"metadata": {"title": "Clip", "description": "Original", "hashtags": []}}))
    calls = []

    def request(req):
        if req.url.path.endswith("/channels"):
            return httpx.Response(200, json={"items": [{"id": channel, "snippet": {"title": "Tio"}}]})
        return httpx.Response(200, json={"items": [{"status": {"privacyStatus": privacy},
            "processingDetails": {"processingStatus": processing}, "snippet": {"title": "Clip"}}]})

    class Publisher:
        def __init__(self, account):
            assert account.privacy == "private"

        def client(self):
            return httpx.AsyncClient(transport=httpx.MockTransport(request))

        async def authenticate(self, client):
            if auth_error:
                raise ValueError("invalid_grant")

        async def publish(self, path, meta):
            assert Queue(cfg.data_dir / "queue.sqlite3").rows()[0]["state"] == "uncertain"
            calls.append(path)
            if upload_error:
                raise TimeoutError()
            return "processing", "remote123"

    monkeypatch.setattr(desktop, "Publisher", Publisher)
    return cfg, clip, manifest, calls


def test_private_success_and_content_dedup_across_paths(tmp_path, monkeypatch):
    cfg, clip, manifest, calls = setup(tmp_path, monkeypatch)
    first = asyncio.run(desktop.upload_private(cfg, clip, manifest))
    other = tmp_path / "copy.mp4"
    other.write_bytes(clip.read_bytes())
    second = asyncio.run(desktop.upload_private(cfg, other, manifest))
    assert first["privacy"] == second["privacy"] == "private"
    assert first["remote_id"] == second["remote_id"] == "remote123"
    assert len(calls) == 1
    assert Queue(cfg.data_dir / "queue.sqlite3").rows()[0]["state"] == "held"
    payload = json.loads(manifest.read_text())
    assert payload["uploaded"] and not payload["published"]


@pytest.mark.parametrize("options", [{"auth_error": True}, {"channel": "wrong-channel"}])
def test_preflight_failure_never_reserves_or_uploads(tmp_path, monkeypatch, options):
    cfg, clip, manifest, calls = setup(tmp_path, monkeypatch, **options)
    with pytest.raises(ValueError):
        asyncio.run(desktop.upload_private(cfg, clip, manifest))
    assert Queue(cfg.data_dir / "queue.sqlite3").rows() == []
    assert calls == []


def test_unknown_upload_is_never_retried(tmp_path, monkeypatch):
    cfg, clip, manifest, calls = setup(tmp_path, monkeypatch, upload_error=True)
    with pytest.raises(TimeoutError):
        asyncio.run(desktop.upload_private(cfg, clip, manifest))
    with pytest.raises(ValueError, match="Uncertain"):
        asyncio.run(desktop.upload_private(cfg, clip, manifest))
    assert len(calls) == 1
    assert Queue(cfg.data_dir / "queue.sqlite3").rows()[0]["state"] == "uncertain"


def test_pending_processing_preserves_id(tmp_path, monkeypatch):
    cfg, clip, manifest, calls = setup(tmp_path, monkeypatch, processing="processing")
    assert asyncio.run(desktop.upload_private(cfg, clip, manifest, poll_attempts=1))["processing"] == "pending"
    asyncio.run(desktop.upload_private(cfg, clip, manifest, poll_attempts=1))
    assert len(calls) == 1
    assert json.loads(manifest.read_text())["remote_id"] == "remote123"


def test_unexpected_public_privacy_is_not_success(tmp_path, monkeypatch):
    cfg, clip, manifest, calls = setup(tmp_path, monkeypatch, privacy="public")
    with pytest.raises(ValueError, match="Unexpected privacy"):
        asyncio.run(desktop.upload_private(cfg, clip, manifest))
    assert len(calls) == 1
    assert not (cfg.data_dir / "receipts").exists()


def test_migration_history_blocks_duplicate(tmp_path, monkeypatch):
    cfg, clip, manifest, calls = setup(tmp_path, monkeypatch)
    cfg.data_dir.mkdir()
    (cfg.data_dir / "migration-history.json").write_text(json.dumps({
        "jobs": [{"clip_sha256": desktop.fingerprint(clip), "remote_id": "old-id"}]}))
    with pytest.raises(ValueError, match="former VPS"):
        asyncio.run(desktop.upload_private(cfg, clip, manifest))
    assert calls == []


def test_hash_and_scheduled_release_are_rejected(tmp_path, monkeypatch):
    cfg, clip, manifest, calls = setup(tmp_path, monkeypatch)
    payload = json.loads(manifest.read_text())
    payload["clip_sha256"] = "bad"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="checksum"):
        asyncio.run(desktop.upload_private(cfg, clip, manifest))
    del payload["clip_sha256"]
    payload["metadata"]["publish_at"] = "2030-01-01T12:00:00Z"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Scheduled"):
        asyncio.run(desktop.upload_private(cfg, clip, manifest))
    assert calls == []


def test_desktop_settings_ignore_former_vps_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_DATA_DIR", "/data")
    monkeypatch.setenv("CLIPPER_OUTBOX_DIR", "/vps/outbox")
    monkeypatch.setenv("CLIPPER_MANAGER_RENDER", "true")
    cfg, *_ = setup(tmp_path, monkeypatch)
    settings = cfg.settings()
    assert settings.data_dir == cfg.data_dir
    assert settings.outbox_dir is None
    assert settings.manager_render is False
    assert settings.review is True
    assert settings.whisper_model == "small"


def test_standard_cli_uses_desktop_config(tmp_path, monkeypatch):
    from clipper.cli import pipeline
    cfg, *_ = setup(tmp_path, monkeypatch)
    cfg.data_dir.mkdir()
    (cfg.data_dir / "config.json").write_text(cfg.model_dump_json())
    monkeypatch.chdir(tmp_path)
    # The standard entrypoint detects data/desktop/config.json.
    expected = tmp_path / "data" / "desktop"
    expected.mkdir()
    (expected / "config.json").write_text(cfg.model_dump_json())
    agent = pipeline()
    assert agent.root == cfg.data_dir
    assert agent.accounts["yt"].privacy == "private"
    assert agent.cfg.outbox_dir is None


def test_oauth_requires_desktop_json_and_preserves_env(tmp_path):
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"web": {"client_id": "id", "client_secret": "secret"}}))
    with pytest.raises(ValueError, match="Desktop app"):
        desktop_client(client)
    env = tmp_path / "oauth.env"
    env.write_text("OTHER=unchanged\n")
    save_env(env, {"YOUTUBE_REFRESH_TOKEN": "test-only-token"})
    from dotenv import dotenv_values
    assert dotenv_values(env) == {"OTHER": "unchanged", "YOUTUBE_REFRESH_TOKEN": "test-only-token"}
