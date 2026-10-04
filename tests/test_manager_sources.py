import asyncio
from unittest.mock import AsyncMock

import httpx

from clipper.config import Account
from clipper.manager_config import ManagerConfig
from clipper.youtube_insights import YouTubeInsights


def test_auto_sources_recheck_license_and_exclude_own_channel():
    api = YouTubeInsights(Account(id='a', platform='youtube', token_env='T'))
    api.publisher.authenticate = AsyncMock()
    def transport(request):
        if request.url.path.endswith('/channels'):
            return httpx.Response(200, json={'items': [{'id': 'mine'}]})
        if request.url.path.endswith('/search'):
            assert request.url.params['videoLicense'] == 'creativeCommon'
            return httpx.Response(200, json={'items': [{'id': {'videoId': x}} for x in ['cc', 'standard', 'own']]})
        return httpx.Response(200, json={'items': [
            {'id': 'cc', 'status': {'license': 'creativeCommon'}, 'snippet': {'title': 'Ronaldo interview', 'channelId': 'source', 'channelTitle': 'Creator'}},
            {'id': 'standard', 'status': {'license': 'youtube'}, 'snippet': {'title': 'Standard', 'channelId': 'source'}},
            {'id': 'own', 'status': {'license': 'creativeCommon'}, 'snippet': {'title': 'Own', 'channelId': 'mine'}}]})
    api.publisher.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport))
    rows = asyncio.run(api.licensed_sources(ManagerConfig(topics=['Ronaldo'])))
    assert len(rows) == 1 and rows[0].url.endswith('v=cc')
    assert rows[0].approved and 'Creator' in rows[0].attribution and 'Changes:' in rows[0].attribution
