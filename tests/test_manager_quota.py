import time
from datetime import datetime, timezone
from pathlib import Path

from clipper.config import Account
from clipper.scheduler import Queue


def test_manual_upload_consumes_daily_slot(tmp_path):
    q = Queue(tmp_path / 'q.db')
    account = Account(id='one', platform='youtube', token_env='T', timezone='UTC', daily_limit=1)
    now = time.time()
    q.observe_posts('one', [{'id': 'manual', 'published_at': datetime.fromtimestamp(now, timezone.utc).isoformat()}])
    job = q.enqueue(account, Path('new.mp4'), {}, False)
    row = next(r for r in q.rows() if r['id'] == job)
    assert datetime.fromtimestamp(row['due'], timezone.utc).date() > datetime.now(timezone.utc).date()


def test_manual_upload_after_reservation_rebooks_before_claim(tmp_path):
    q = Queue(tmp_path / 'q.db')
    account = Account(id='one', platform='youtube', token_env='T', timezone='UTC', daily_limit=1)
    job = q.enqueue(account, Path('new.mp4'), {}, False)
    with q.connect() as db:
        db.execute('UPDATE jobs SET due=? WHERE id=?', (time.time(), job))
    q.observe_posts('one', [{'id': 'manual', 'published_at': datetime.now(timezone.utc).isoformat()}])
    assert q.claim({'one': account}) is None
    assert q.rows()[0]['state'] == 'queued'


def test_queued_and_observed_upload_count_once(tmp_path):
    q = Queue(tmp_path / 'q.db')
    account = Account(id='one', platform='youtube', token_env='T', warmed=True, daily_limit=2, timezone='UTC')
    job = q.enqueue(account, Path('old.mp4'), {}, False)
    q.set_state(job, 'published', 'remote')
    now = time.time()
    with q.connect() as db:
        db.execute('UPDATE jobs SET due=? WHERE id=?', (now, job))
    q.observe_posts('one', [{'id': 'remote', 'published_at': datetime.fromtimestamp(now, timezone.utc).isoformat()}])
    with q.connect() as db:
        assert q._observed_count(db, 'one', now-1, now+1) == 0
