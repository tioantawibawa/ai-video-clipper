import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from clipper.scheduler import Queue


def test_immediate_upload_needs_authorization_and_deduplicates(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('publish_now',Path(__file__).parents[1]/'scripts/publish_now.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    accounts=tmp_path/'accounts.json'
    accounts.write_text(json.dumps([{'id':'yt','platform':'youtube','token_env':'T','privacy':'public'}]))
    clip=tmp_path/'video.mp4'; clip.write_bytes(b'video')
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'metadata':{'title':'News','description':'Original','hashtags':[]}}))
    database=tmp_path/'queue.db'
    calls=[]
    class FakePublisher:
        def __init__(self,account): pass
        async def publish(self,path,meta):
            assert Queue(database).rows()[0]['state']=='uncertain'
            calls.append(path)
            return 'processing','news123'
        def client(self):
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={
                'items':[{'status':{'privacyStatus':'public'},'processingDetails':{'processingStatus':'succeeded'},
                          'snippet':{'publishedAt':'2026-10-06T12:00:00Z'}}]})))
        async def authenticate(self,client): pass
    monkeypatch.setattr(module,'Publisher',FakePublisher)
    monkeypatch.setattr(module,'load_dotenv',lambda *args:None)
    with pytest.raises(ValueError,match='authorization'):
        asyncio.run(module.publish(clip,manifest,accounts,database,'yt'))
    asyncio.run(module.publish(clip,manifest,accounts,database,'yt',True))
    asyncio.run(module.publish(clip,manifest,accounts,database,'yt',True))
    assert len(calls)==1
    assert Queue(database).rows()[0]['state']=='published'
