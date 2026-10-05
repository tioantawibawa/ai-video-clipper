import json
import random
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, time as day_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Account


class Queue:
    """Single-host durable queue. BEGIN IMMEDIATE serializes reservations/claims."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, account TEXT NOT NULL, clip TEXT NOT NULL,
                    metadata TEXT NOT NULL, state TEXT NOT NULL, due REAL NOT NULL,
                    updated REAL NOT NULL, remote_id TEXT, error TEXT,
                    UNIQUE(account, clip)
                );
                CREATE INDEX IF NOT EXISTS due_jobs ON jobs(state,due);
                CREATE TABLE IF NOT EXISTS episodes (
                    url TEXT PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observed_posts (
                    account TEXT NOT NULL, remote_id TEXT NOT NULL, published REAL NOT NULL,
                    PRIMARY KEY(account,remote_id)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _reserve(self, db, account: Account, now: float) -> float:
        zone = ZoneInfo(account.timezone)
        local = datetime.fromtimestamp(now, zone)
        limit = account.daily_limit if account.warmed else 1
        for day in range(3660):
            date = local.date() + timedelta(days=day)
            start = datetime.combine(date, day_time.min, zone).timestamp()
            end = datetime.combine(date + timedelta(days=1), day_time.min, zone).timestamp()
            # Unknown outcomes consume quota until manually reconciled.
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE account=? AND due>=? AND due<? "
                               "AND state NOT IN ('rejected','failed')", (account.id, start, end)).fetchone()[0]
            count += self._observed_count(db, account.id, start, end)
            if count >= limit:
                continue
            latest = db.execute("SELECT MAX(due) FROM jobs WHERE account=? "
                                "AND state NOT IN ('rejected','failed')", (account.id,)).fetchone()[0]
            earliest = max(now + random.uniform(60, 600), start + 8 * 3600,
                           (latest + random.uniform(3 * 3600, 5 * 3600)) if latest else 0)
            if earliest < end - 3600:
                return random.uniform(earliest, min(earliest + 3600, end - 3600))
        raise RuntimeError("No schedule capacity available")

    def enqueue(self, account: Account, clip: Path, metadata: dict, review: bool) -> int:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM jobs WHERE account=? AND clip=?",
                                  (account.id, str(clip.resolve()))).fetchone()
            if existing:
                return existing[0]
            due = self._reserve(db, account, now)
            cursor = db.execute("INSERT INTO jobs(account,clip,metadata,state,due,updated) VALUES(?,?,?,?,?,?)",
                (account.id, str(clip.resolve()), json.dumps(metadata), "review" if review else "queued", due, now))
            return cursor.lastrowid

    def review(self, job_id: int, approve: bool, account: Account):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=? AND account=? AND state='review'",
                             (job_id, account.id)).fetchone()
            if not row:
                raise ValueError("Job is not awaiting review for this account")
            # Release old reservation before assigning a fresh slot.
            db.execute("UPDATE jobs SET state='rejected' WHERE id=?", (job_id,))
            due = self._reserve(db, account, time.time()) if approve else row["due"]
            db.execute("UPDATE jobs SET state=?, due=?,updated=? WHERE id=?",
                       ("queued" if approve else "rejected", due, time.time(), job_id))

    def claim(self, accounts: dict[str, Account]):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM jobs WHERE state='queued' AND due<=? ORDER BY due", (now,)).fetchall()
            for row in rows:
                account = accounts.get(row["account"])
                if not account:
                    continue
                zone = ZoneInfo(account.timezone)
                date = datetime.fromtimestamp(now, zone).date()
                start = datetime.combine(date, day_time.min, zone).timestamp()
                end = datetime.combine(date+timedelta(days=1), day_time.min, zone).timestamp()
                posted = db.execute("SELECT COUNT(*) FROM jobs WHERE account=? AND due>=? AND due<? AND state IN ('published','processing','uploading','uncertain')", (account.id, start, end)).fetchone()[0]
                posted += self._observed_count(db, account.id, start, end)
                if posted >= (account.daily_limit if account.warmed else 1):
                    db.execute("UPDATE jobs SET state='failed' WHERE id=?", (row['id'],))
                    due = self._reserve(db, account, now)
                    db.execute("UPDATE jobs SET state='queued',due=?,updated=? WHERE id=?", (due, now, row['id']))
                    continue
                # Re-book overdue jobs, avoiding catch-up bursts after downtime.
                if row["due"] < now - 300:
                    db.execute("UPDATE jobs SET state='failed' WHERE id=?", (row["id"],))
                    due = self._reserve(db, account, now)
                    db.execute("UPDATE jobs SET state='queued',due=?,updated=? WHERE id=?", (due, now, row["id"]))
                    continue
                busy = db.execute("SELECT 1 FROM jobs WHERE account=? AND state IN ('uploading','processing','uncertain')",
                                  (account.id,)).fetchone()
                if busy:
                    continue
                db.execute("UPDATE jobs SET state='uploading',updated=? WHERE id=?", (now, row["id"]))
                return dict(row)
        return None

    @staticmethod
    def _observed_count(db, account, start, end):
        return db.execute("SELECT COUNT(*) FROM observed_posts p WHERE account=? AND published>=? AND published<? "
                          "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.account=p.account AND j.remote_id=p.remote_id "
                          "AND j.state IN ('published','processing','uploading','uncertain','scheduled','held'))", (account, start, end)).fetchone()[0]

    def observe_posts(self, account, videos):
        """Count verified platform uploads, including those posted manually outside the queue."""
        with self.connect() as db:
            for video in videos:
                stamp = datetime.fromisoformat(video['published_at'].replace('Z', '+00:00')).timestamp()
                db.execute("INSERT INTO observed_posts VALUES(?,?,?) ON CONFLICT(account,remote_id) DO UPDATE SET published=excluded.published",
                           (account, video['id'], stamp))

    def set_state(self, job_id: int, state: str, remote_id: str | None = None, error: str | None = None):
        if state not in {"published", "processing", "uncertain", "failed", "scheduled", "held"}:
            raise ValueError("Invalid result state")
        with self.connect() as db:
            db.execute("UPDATE jobs SET state=?,remote_id=COALESCE(?,remote_id),error=?,updated=? WHERE id=?",
                       (state, remote_id, error, time.time(), job_id))

    def recover(self):
        with self.connect() as db:
            # Never automatically retry a request that could already have posted.
            db.execute("UPDATE jobs SET state='uncertain' WHERE state='uploading'")
            db.execute("UPDATE episodes SET state='failed' WHERE state='processing'")

    def rows(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY id")]

    def episode_claim(self, url: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM episodes WHERE url=?", (url,)).fetchone()
            if row and row[0] in {"done", "processing"}:
                return False
            db.execute("INSERT INTO episodes VALUES(?,'processing',?) ON CONFLICT(url) "
                       "DO UPDATE SET state='processing', updated=excluded.updated", (url, time.time()))
            return True

    def episode_finish(self, url: str, success: bool):
        with self.connect() as db:
            db.execute("UPDATE episodes SET state=?,updated=? WHERE url=?",
                       ("done" if success else "failed", time.time(), url))


def utc_label(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
