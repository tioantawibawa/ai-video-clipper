import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from clipper.config import Account
from clipper.scheduler import Queue
from clipper.daily_worker import eligible


def test_fixed_daily_slot_rolls_over_and_deduplicates(monkeypatch,tmp_path):
    zone=ZoneInfo('Asia/Jakarta')
    monkeypatch.setattr('clipper.scheduler.time.time',lambda:datetime(2026,10,7,18,0,tzinfo=zone).timestamp())
    account=Account(id='youtube',platform='youtube',token_env='TOKEN',timezone='Asia/Jakarta',publish_time='19:10')
    queue=Queue(tmp_path/'queue.sqlite3')
    first=queue.enqueue(account,tmp_path/'first.mp4',{},False)
    assert queue.enqueue(account,tmp_path/'first.mp4',{},False)==first
    queue.enqueue(account,tmp_path/'second.mp4',{},False)
    assert [datetime.fromtimestamp(r['due'],zone).isoformat() for r in queue.rows()]==[
        '2026-10-07T19:10:00+07:00','2026-10-08T19:10:00+07:00']
    monkeypatch.setattr('clipper.scheduler.time.time',lambda:datetime(2026,10,9,19,11,tzinfo=zone).timestamp())
    queue.enqueue(account,tmp_path/'third.mp4',{},False)
    assert datetime.fromtimestamp(queue.rows()[-1]['due'],zone).isoformat()=='2026-10-10T19:10:00+07:00'


def test_fixed_slot_honors_unknown_upload_quota(monkeypatch,tmp_path):
    zone=ZoneInfo('Asia/Jakarta')
    now=datetime(2026,10,7,18,0,tzinfo=zone).timestamp()
    monkeypatch.setattr('clipper.scheduler.time.time',lambda:now)
    account=Account(id='youtube',platform='youtube',token_env='TOKEN',timezone='Asia/Jakarta',publish_time='19:10')
    queue=Queue(tmp_path/'queue.sqlite3')
    job=queue.enqueue(account,tmp_path/'first.mp4',{},False)
    queue.set_state(job,'uncertain')
    queue.enqueue(account,tmp_path/'second.mp4',{},False)
    assert datetime.fromtimestamp(queue.rows()[-1]['due'],zone).day==8


def test_published_sources_not_selected_again():
    assert eligible([{'id':'old'},{'id':'new'}],{'old'})==[{'id':'new'}]


def test_invalid_wall_clock_time_rejected():
    with pytest.raises(ValueError):
        Account(id='one',platform='youtube',token_env='TOKEN',publish_time='25:99')
