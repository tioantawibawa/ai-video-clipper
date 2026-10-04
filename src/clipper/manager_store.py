"""Durable snapshots and comment replies; uncertain sends never retry."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class ManagerStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY, kind TEXT, created REAL, payload TEXT);
                CREATE TABLE IF NOT EXISTS replies (
                    comment_id TEXT PRIMARY KEY, video_id TEXT NOT NULL,
                    original TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL,
                    remote_id TEXT, created REAL NOT NULL, updated REAL NOT NULL);
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

    def snapshot(self, kind, payload):
        with self.connect() as db:
            db.execute("INSERT INTO snapshots(kind,created,payload) VALUES(?,?,?)",
                       (kind, time.time(), json.dumps(payload)))
            # Keep a bounded 90-day history of public aggregate metrics.
            db.execute("DELETE FROM snapshots WHERE created<?", (time.time() - 90*86400,))

    def latest(self, kind):
        with self.connect() as db:
            row = db.execute("SELECT payload,created FROM snapshots WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
            return (json.loads(row[0]), row[1]) if row else (None, None)

    def reply_exists(self, comment_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM replies WHERE comment_id=?", (comment_id,)).fetchone() is not None

    def draft(self, comment_id, video_id, original, text):
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO replies VALUES(?,?,?,?, 'draft',NULL,?,?)",
                       (comment_id, video_id, original, text, now, now))

    def replies(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM replies ORDER BY created DESC")]

    def claim_reply(self, comment_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM replies WHERE comment_id=? AND state='draft'", (comment_id,)).fetchone()
            if not row:
                raise ValueError("Reply is not a draft; inspect uncertain outcomes before retrying")
            db.execute("UPDATE replies SET state='sending',updated=? WHERE comment_id=?", (time.time(), comment_id))
            return dict(row)

    def reply_state(self, comment_id, state, remote_id=None):
        if state not in {"sent", "rejected", "uncertain"}:
            raise ValueError("Invalid reply outcome")
        with self.connect() as db:
            db.execute("UPDATE replies SET state=?,remote_id=COALESCE(?,remote_id),updated=? WHERE comment_id=?",
                       (state, remote_id, time.time(), comment_id))

    def reject(self, comment_id):
        with self.connect() as db:
            changed = db.execute("UPDATE replies SET state='rejected',updated=? WHERE comment_id=? AND state='draft'",
                                 (time.time(), comment_id)).rowcount
            if not changed:
                raise ValueError("Only draft replies may be rejected")

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE replies SET state='uncertain' WHERE state='sending'")
