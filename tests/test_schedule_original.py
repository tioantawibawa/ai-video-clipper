import asyncio
import importlib.util
import json
from pathlib import Path

import httpx

from clipper.scheduler import Queue


def test_reserve_upload_verify_and_repeat_without_duplicate(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("schedule_original", Path(__file__).parents[1]/"scripts/schedule_original.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    accounts = tmp_path/"accounts.json"
    accounts.write_text(json.dumps([{"id":"test", "platform":"youtube", "token_env":"T", "privacy":"public"}]))
    manifest = tmp_path/"manifest.json"
    manifest.write_text(json.dumps({"metadata":{"title":"Test", "description":"Original", "hashtags":[]}}))
    clip = tmp_path/"video.mp4"
    clip.write_bytes(b"media")
    database = tmp_path/"queue.db"
    calls = []
    class FakePublisher:
        def __init__(self, account):
            pass
        async def publish(self, path, meta):
            # Reservation is already uncertain before the non-idempotent upload.
            assert Queue(database).rows()[0]["state"] == "uncertain"
            calls.append(meta)
            return "processing", "original123"
        def client(self):
            def handler(request):
                return httpx.Response(200, json={"items":[{"status":{
                    "privacyStatus":"private", "publishAt":calls[0]["publish_at"]}}]})
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async def authenticate(self, client):
            pass
    monkeypatch.setattr(module, "Publisher", FakePublisher)
    monkeypatch.setattr(module, "load_dotenv", lambda *args: None)
    asyncio.run(module.schedule(clip, manifest, accounts, database, "test"))
    rows = Queue(database).rows()
    assert rows[0]["state"] == "scheduled"
    assert rows[0]["remote_id"] == "original123"
    asyncio.run(module.schedule(clip, manifest, accounts, database, "test"))
    assert len(calls) == 1
    queue = Queue(database)
    queue.recover()
    assert queue.rows()[0]["state"] == "scheduled"
