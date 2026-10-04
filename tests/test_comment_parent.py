import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from clipper.config import Account
from clipper.youtube_insights import YouTubeInsights


@pytest.mark.parametrize("video,reply_count,allowed", [("v", 0, True), ("other", 0, False), ("v", 1, False)])
def test_reply_uses_thread_video_association(video, reply_count, allowed):
    api = YouTubeInsights(Account(id="a", platform="youtube", token_env="T"))
    calls = []
    async def handler(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "remote-reply"})
        assert request.url.path.endswith("commentThreads")
        return httpx.Response(200, json={"items": [{"snippet": {
            "videoId": video, "canReply": True, "totalReplyCount": reply_count,
            "topLevelComment": {"id": "comment"}}}]})
    api.publisher.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api.publisher.authenticate = AsyncMock()
    api.channel = AsyncMock(return_value={"id": "channel"})
    api.video_items = AsyncMock(return_value=[{"snippet": {"channelId": "channel"}}])
    if allowed:
        assert asyncio.run(api.reply("v", "comment", "Thanks!")) == "remote-reply"
    else:
        with pytest.raises(ValueError):
            asyncio.run(api.reply("v", "comment", "Thanks!"))
        assert len(calls) == 1
