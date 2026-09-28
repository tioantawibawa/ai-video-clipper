import asyncio
import json

import httpx
import pytest

from clipper.config import Account
from clipper.publisher import Publisher, checked, upload_url


def test_api_errors_even_when_http_200():
    response = httpx.Response(200, json={"error": {"code": "access_token_invalid"}},
                              request=httpx.Request("POST", "https://example.com"))
    with pytest.raises(RuntimeError):
        checked(response)


def test_upload_host_validation():
    with pytest.raises(ValueError):
        upload_url("https://googleapis.com.attacker.test/upload", "googleapis.com")
    assert upload_url("https://www.googleapis.com/upload", "googleapis.com")


def test_youtube_streaming_and_status(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video data")
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["status"]["privacyStatus"] == "private"
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/session"})
        assert await request.aread() == b"video data"
        return httpx.Response(200, json={"id": "video123"})

    async def test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await Publisher(Account(id="test", platform="youtube", token_env="TOKEN")).youtube(
                client, clip, {"title": "Hi", "description": "Description", "hashtags": ["#podcast"]})

    assert asyncio.run(test()) == ("processing", "video123")
    assert len(requests) == 2


def test_tiktok_draft_signed_upload_and_inbox_status(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    async def handler(request):
        if request.url.path.endswith("inbox/video/init/"):
            assert "post_info" not in json.loads(request.content)
            return httpx.Response(200, json={"data": {"publish_id": "draft1",
                "upload_url": "https://open-upload.tiktokapis.com/video/"}})
        if request.method == "PUT":
            assert "Authorization" not in request.headers
            assert await request.aread() == b"video"
            return httpx.Response(201)
        return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}})
    publisher = Publisher(Account(id="test", platform="tiktok", token_env="TOKEN"))
    monkeypatch.setattr(publisher, "client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer test"}))
    assert asyncio.run(publisher.publish(clip, {})) == ("processing", "draft1")
    assert asyncio.run(publisher.poll("draft1")) == ("awaiting_creator", "draft1")
