"""Explicit one-off YouTube publication; normal daily scheduling stays unchanged."""
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


async def publish(clip, manifest, accounts, database, account_id, allow_extra=False):
    load_dotenv('.env')
    account = next(Account.model_validate(a) for a in json.loads(accounts.read_text()) if a['id'] == account_id)
    if account.platform != 'youtube' or account.privacy != 'public':
        raise ValueError('Immediate publication requires the designated public YouTube account')
    if not allow_extra:
        raise ValueError('Explicit --allow-extra-publication authorization is required for an immediate upload')
    payload = json.loads(manifest.read_text())
    metadata = Metadata.model_validate(payload['metadata']).model_dump()
    queue = Queue(database)
    with queue.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM jobs WHERE account=? AND clip=?',
                              (account.id, str(clip.resolve()))).fetchone()
        if existing:
            print(json.dumps({'job_id': existing['id'], 'state': existing['state'],
                              'remote_id': existing['remote_id'], 'existing': True}))
            return
        if payload.get('remote_id'):
            raise ValueError('Manifest already references an upload; reconcile before publishing')
        now = datetime.now(timezone.utc).timestamp()
        job = db.execute('INSERT INTO jobs(account,clip,metadata,state,due,updated) VALUES(?,?,?,?,?,?)',
            (account.id,str(clip.resolve()),json.dumps(metadata),'uncertain',now,now)).lastrowid
    publisher = Publisher(account)
    try:
        state, remote_id = await publisher.publish(clip, metadata)
    except Exception as exc:
        queue.set_state(job, 'uncertain', error=type(exc).__name__)
        raise
    queue.set_state(job, 'processing', remote_id)
    payload.update(job_id=job, remote_id=remote_id, state='processing', one_off_publication=True)
    manifest.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    async with publisher.client() as client:
        await publisher.authenticate(client)
        for attempt in range(40):
            response = await client.get('https://www.googleapis.com/youtube/v3/videos',
                params={'part':'status,processingDetails,snippet','id':remote_id})
            response.raise_for_status()
            items = response.json().get('items',[])
            if items:
                video = items[0]
                status = video['status']
                processing = video.get('processingDetails',{}).get('processingStatus')
                if processing in {'failed','terminated'}:
                    queue.set_state(job,'failed',remote_id)
                    raise RuntimeError('YouTube could not process this upload')
                if status.get('privacyStatus') == 'public' and processing == 'succeeded':
                    queue.set_state(job,'published',remote_id)
                    payload.update(state='published',published=True,published_at=video['snippet']['publishedAt'])
                    manifest.write_text(json.dumps(payload,indent=2),encoding='utf-8')
                    print(json.dumps({'job_id':job,'remote_id':remote_id,'state':'published',
                        'privacy':'public','url':'https://www.youtube.com/shorts/'+remote_id}))
                    return
            await asyncio.sleep(3)
    print(json.dumps({'job_id':job,'remote_id':remote_id,'state':'processing','existing':False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('clip',type=Path)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--accounts',type=Path,default=Path('data/vps/accounts.json'))
    parser.add_argument('--queue',type=Path,default=Path('data/vps/queue.sqlite3'))
    parser.add_argument('--account',default='podcast-us-youtube')
    parser.add_argument('--allow-extra-publication',action='store_true')
    args = parser.parse_args()
    asyncio.run(publish(args.clip,args.manifest,args.accounts,args.queue,args.account,args.allow_extra_publication))
