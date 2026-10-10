"""Atomically reserve explicit daily slots for a reviewed local clip batch."""
from datetime import datetime, time as day_time, timedelta
import hashlib
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from .models import Metadata


def reserve_batch(queue, account, root: Path, plan: list[dict]):
    root = root.resolve()
    zone = ZoneInfo(account.timezone)
    now = time.time()
    prepared, dates = [], set()
    for entry in plan:
        clip = (root / entry['clip']).resolve()
        manifest = (root / entry['manifest']).resolve()
        if not clip.is_relative_to(root) or not manifest.is_relative_to(root):
            raise ValueError('Batch file escapes its root')
        payload = json.loads(manifest.read_text(encoding='utf-8'))
        meta = Metadata.model_validate(payload['metadata']).model_dump()
        due = datetime.fromisoformat(entry['publish_at'])
        if due.tzinfo is None or due.timestamp() <= now:
            raise ValueError('Batch release must be a future timezone-aware timestamp')
        local = due.astimezone(zone)
        if local.date() in dates:
            raise ValueError('Only one batch video per local day')
        dates.add(local.date())
        if account.publish_time and local.strftime('%H:%M') != account.publish_time:
            raise ValueError('Release must match the account publishing slot')
        with clip.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if payload.get('clip_sha256') != digest or not payload.get('qa_approved'):
            raise ValueError('Batch requires reviewed media and a matching SHA256')
        if payload.get('published') or payload.get('remote_id'):
            raise ValueError('Batch manifest already has a publication receipt')
        prepared.append((clip, meta, local, due.timestamp()))
    results = []
    with queue.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for clip, meta, local, due in prepared:
            existing = db.execute('SELECT * FROM jobs WHERE account=? AND clip=?', (account.id, str(clip))).fetchone()
            if existing:
                if abs(existing['due']-due) > 1:
                    raise ValueError('Existing job has a different schedule; reconcile first')
                results.append(dict(existing))
                continue
            start = datetime.combine(local.date(), day_time.min, zone).timestamp()
            end = datetime.combine(local.date()+timedelta(days=1), day_time.min, zone).timestamp()
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE account=? AND due>=? AND due<? AND state NOT IN ('rejected','failed')", (account.id, start, end)).fetchone()[0]
            if count + queue._observed_count(db, account.id, start, end):
                raise ValueError('Daily slot already reserved; batch rolled back')
            cursor = db.execute('INSERT INTO jobs(account,clip,metadata,state,due,updated) VALUES(?,?,?,?,?,?)', (account.id, str(clip), json.dumps(meta), 'queued', due, now))
            results.append(dict(id=cursor.lastrowid, state='queued', due=due, clip=str(clip)))
    return results
