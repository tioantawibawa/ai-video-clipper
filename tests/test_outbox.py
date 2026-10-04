import asyncio
import json

import httpx

from clipper.config import Account, Settings
from clipper.pipeline import Pipeline
from clipper.publisher import Publisher


def test_outbox_deduplicates_and_skips_uploaded_and_traversal(tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps([{"id": "yt", "platform": "youtube", "token_env": "TOKEN"}]))
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "clip.mp4").write_bytes(b"media")
    payload = {"file": "clip.mp4", "metadata": {"title": "Earth", "description": "NASA", "hashtags": ["#Earth"]}}
    (inbox / "ready.json").write_text(json.dumps(payload))
    (inbox / "uploaded.json").write_text(json.dumps({**payload, "published": True}))
    (inbox / "escape.json").write_text(json.dumps({**payload, "file": "../outside.mp4"}))
    agent = Pipeline(Settings(data_dir=tmp_path / "data", accounts_file=accounts,
                              outbox_dir=inbox, outbox_account="yt"))
    agent.scan_outbox()
    agent.scan_outbox()
    rows = agent.queue.rows()
    assert len(rows) == 1
    assert rows[0]["state"] == "queued"


def test_refresh_uses_account_env_and_no_bearer(monkeypatch):
    for key, value in {"RT": "refresh", "CID": "client", "CS": "secret"}.items():
        monkeypatch.setenv(key, value)
    account = Account(id="yt", platform="youtube", token_env="TOKEN", refresh_token_env="RT",
                      client_id_env="CID", client_secret_env="CS")
    def handler(request):
        assert str(request.url) == "https://oauth2.googleapis.com/token"
        assert "Authorization" not in request.headers
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(200, json={"access_token": "new-token"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                    headers={"Authorization": "Bearer old"}) as client:
            await Publisher(account).authenticate(client)
            assert client.headers["Authorization"] == "Bearer new-token"
    asyncio.run(run())
