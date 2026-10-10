"""VPS CLI: reserve a reviewed batch; the daemon publishes each clip at its slot."""
import argparse
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from clipper.batch_queue import reserve_batch
from clipper.config import Account
from clipper.scheduler import Queue


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('plan',type=Path)
    p.add_argument('--accounts',type=Path,default=Path('data/vps/accounts.json'))
    p.add_argument('--queue',type=Path,default=Path('data/vps/queue.sqlite3'))
    p.add_argument('--account',default='podcast-us-youtube')
    args=p.parse_args()
    account=next(Account.model_validate(a) for a in json.loads(args.accounts.read_text()) if a['id']==args.account)
    result=reserve_batch(Queue(args.queue),account,args.plan.parent,json.loads(args.plan.read_text()))
    print(json.dumps([dict(id=r['id'],state=r['state'],publish_at=datetime.fromtimestamp(r['due'],ZoneInfo(account.timezone)).isoformat()) for r in result]))


if __name__=='__main__':
    main()
