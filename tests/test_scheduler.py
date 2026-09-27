import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from clipper.config import Account
from clipper.scheduler import Queue


def account(warmed=False):
    return Account(id="one", platform="youtube", token_env="TOKEN", warmed=warmed, daily_limit=4)


def test_limits_idempotency_and_concurrent_reservations(tmp_path):
    queue = Queue(tmp_path / "queue.db")
    acc = account()
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda i: queue.enqueue(acc, tmp_path / f"{i}.mp4", {}, False), range(8)))
    assert len(set(ids)) == 8
    dates = Counter(datetime.fromtimestamp(r["due"], ZoneInfo(acc.timezone)).date() for r in queue.rows())
    assert max(dates.values()) == 1
    assert queue.enqueue(acc, tmp_path / "0.mp4", {}, False) in ids
    assert len(queue.rows()) == 8


def test_warmed_limit_and_spacing(tmp_path):
    queue = Queue(tmp_path / "queue.db")
    acc = account(True)
    for i in range(16):
        queue.enqueue(acc, tmp_path / f"{i}.mp4", {}, False)
    rows = queue.rows()
    dates = Counter(datetime.fromtimestamp(r["due"], ZoneInfo(acc.timezone)).date() for r in rows)
    assert max(dates.values()) <= 4
    dues = sorted(r["due"] for r in rows)
    assert all(b - a >= 3 * 3600 for a, b in zip(dues, dues[1:]))


def test_claim_atomic_and_crash_uncertain(tmp_path):
    queue = Queue(tmp_path / "queue.db")
    acc = account()
    job = queue.enqueue(acc, tmp_path / "clip.mp4", {}, False)
    with queue.connect() as db:
        db.execute("UPDATE jobs SET due=? WHERE id=?", (time.time() - 1, job))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: queue.claim({acc.id: acc}), range(2)))
    assert sum(x is not None for x in results) == 1
    queue.recover()
    assert queue.rows()[0]["state"] == "uncertain"
    assert queue.claim({acc.id: acc}) is None


def test_review_and_overdue_rebooking(tmp_path):
    queue = Queue(tmp_path / "queue.db")
    acc = account()
    job = queue.enqueue(acc, tmp_path / "clip.mp4", {}, True)
    assert queue.claim({acc.id: acc}) is None
    queue.review(job, True, acc)
    with queue.connect() as db:
        db.execute("UPDATE jobs SET due=?", (time.time() - 86400,))
    assert queue.claim({acc.id: acc}) is None
    assert queue.rows()[0]["due"] > time.time()
