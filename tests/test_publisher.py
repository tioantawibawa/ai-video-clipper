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
