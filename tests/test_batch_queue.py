import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from clipper.batch_queue import reserve_batch
from clipper.config import Account
from clipper.scheduler import Queue


def setup(tmp_path):
    account=Account(id='yt',platform='youtube',token_env='TOKEN',timezone='Asia/Jakarta',publish_time='19:10')
    queue=Queue(tmp_path/'queue.sqlite3')
    date=datetime.now(ZoneInfo(account.timezone)).date()+timedelta(days=1)
    plan=[]
    for i in range(5):
        clip=tmp_path/f'{i}.mp4';clip.write_bytes(b'media'+bytes([i]))
        meta={'title':f'Clip {i}','description':'Reviewed excerpt','hashtags':[]}
        manifest=tmp_path/f'{i}.json';manifest.write_text(json.dumps(dict(metadata=meta,qa_approved=True,clip_sha256=hashlib.sha256(clip.read_bytes()).hexdigest())))
        due=datetime.combine(date+timedelta(days=i),datetime.strptime('19:10','%H:%M').time(),ZoneInfo(account.timezone))
        plan.append(dict(clip=clip.name,manifest=manifest.name,publish_at=due.isoformat()))
    return account,queue,plan


def test_five_daily_slots_and_idempotent_retry(tmp_path):
    a,q,p=setup(tmp_path)
    ids=[r['id'] for r in reserve_batch(q,a,tmp_path,p)]
    assert [r['id'] for r in reserve_batch(q,a,tmp_path,p)]==ids
    assert len(q.rows())==5


def test_collision_rolls_back_whole_batch(tmp_path):
    a,q,p=setup(tmp_path)
    reserve_batch(q,a,tmp_path,[p[2]])
    bad=dict(p[2],clip=p[1]['clip'],manifest=p[1]['manifest'])
    with pytest.raises(ValueError,match='already reserved'):
        reserve_batch(q,a,tmp_path,[p[0],bad])
    assert len(q.rows())==1


def test_tampered_or_unreviewed_media_cannot_queue(tmp_path):
    a,q,p=setup(tmp_path)
    (tmp_path/p[0]['clip']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='SHA256'):
        reserve_batch(q,a,tmp_path,p)
    assert not q.rows()
