"""Run on the VPS: reserve quota, upload private, then let YouTube release on time.

Interrupted uploads consume their reservation and require reconciliation; no blind retries.
This tool never overrides the account's daily quota.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from dotenv import load_dotenv

from clipper.config import Account
from clipper.models import Metadata
from clipper.publisher import Publisher
from clipper.scheduler import Queue


async def schedule(clip, manifest, account_file, queue_file, account_id, thumbnail=None):
    load_dotenv(".env")
    account = next(Account.model_validate(a) for a in json.loads(account_file.read_text()) if a["id"] == account_id)
    payload = json.loads(manifest.read_text())
    meta = Metadata.model_validate(payload["metadata"]).model_dump()
    queue = Queue(queue_file)
    # Reserve and mark before network I/O so the normal daemon cannot also claim it.
    with queue.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM jobs WHERE account=? AND clip=?",
                              (account.id, str(clip.resolve()))).fetchone()
        if existing:
            print(json.dumps({"id": existing["id"], "state": existing["state"], "remote_id": existing["remote_id"],
                              "scheduled_at": datetime.fromtimestamp(existing["due"], timezone.utc).isoformat(),
                              "existing": True, "thumbnail_uploaded": payload.get("thumbnail_uploaded", False)}))
            return
        now = datetime.now(timezone.utc).timestamp()
        # Leave time for processing and Studio checks before public release.
        due = queue._reserve(db, account, now + 900)
        if due < now+900:
            raise ValueError("Reservation too close; reconcile quota before uploading")
        meta["publish_at"] = datetime.fromtimestamp(due, timezone.utc).isoformat()
        row = db.execute("INSERT INTO jobs(account,clip,metadata,state,due,updated) VALUES(?,?,?,?,?,?)",
                         (account.id, str(clip.resolve()), json.dumps(meta), "uncertain", due, now))
        job = row.lastrowid
    publisher = Publisher(account)
    try:
        state, remote_id = await publisher.publish(clip, meta)
    except Exception as exc:
        queue.set_state(job, "uncertain", error=type(exc).__name__)
        raise
    queue.set_state(job, "uncertain", remote_id)
    payload.update(remote_id=remote_id, job_id=job, scheduled_at=meta["publish_at"], published=False)
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    async with publisher.client() as client:
        await publisher.authenticate(client)
        # The read-only list endpoint can lag a successful resumable upload.
        for attempt in range(6):
            response = await client.get("https://www.googleapis.com/youtube/v3/videos",
                                        params={"part": "status", "id": remote_id})
            response.raise_for_status()
            items = response.json().get("items", [])
            if items and items[0]["status"].get("publishAt"):
                break
            if attempt < 5:
                await asyncio.sleep(3)
        if not items or items[0]["status"].get("privacyStatus") != "private":
            raise RuntimeError("Scheduled upload exists but private status requires reconciliation")
        returned = items[0]["status"].get("publishAt")
        if not returned or abs(datetime.fromisoformat(returned.replace("Z", "+00:00")).timestamp()-due) > 1:
            raise RuntimeError("Scheduled upload exists but release time requires reconciliation")
    queue.set_state(job, "scheduled", remote_id)
    if thumbnail:
        try:
            async with publisher.client() as client:
                await publisher.authenticate(client)
                response = await client.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
                    params={"videoId": remote_id}, headers={"Content-Type": "image/jpeg"},
                    content=thumbnail.read_bytes())
                response.raise_for_status()
                payload["thumbnail_uploaded"] = True
                manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            # The video already exists: a thumbnail failure must never trigger a second upload.
            payload["thumbnail_error"] = type(exc).__name__
            manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"id": job, "remote_id": remote_id, "scheduled_at": meta["publish_at"],
                      "state": "scheduled", "thumbnail_uploaded": payload.get("thumbnail_uploaded", False)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--accounts", type=Path, default=Path("data/vps/accounts.json"))
    parser.add_argument("--queue", type=Path, default=Path("data/vps/queue.sqlite3"))
    parser.add_argument("--account", default="podcast-us-youtube")
    parser.add_argument("--thumbnail", type=Path)
    args = parser.parse_args()
    asyncio.run(schedule(args.clip, args.manifest, args.accounts, args.queue, args.account, args.thumbnail))
