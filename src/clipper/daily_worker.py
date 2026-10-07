"""Prepare one licensed Ronaldo podcast clip locally each day; OAuth stays on VPS."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel, Field

from .config import Settings
from .curator import curate
from .downloader import download
from .editor import render
from .local import write_json
from .local_worker import WorkerConfig, deliver, ssh_options
from .metadata import generate
from .pipeline import worker_lock
from .process import run
from .transcriber import transcribe


class DailyConfig(BaseModel):
    worker_file: Path
    output: Path
    channels: list[str] = Field(min_length=1)
    topic: str = "Cristiano Ronaldo"
    prepare_time: str = Field("15:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Asia/Jakarta"
    whisper_model: str = "small"


async def remote_sources(worker, channels, topic, ids=None):
    # The only remote writes happen later through deliver(); this read never exports OAuth.
    arguments = json.dumps({"channels":channels,"topic":topic,"ids":ids})
    code = '''
import asyncio,json,sqlite3,re
from datetime import datetime,timedelta,timezone
from dotenv import load_dotenv
from clipper.config import Account
from clipper.publisher import Publisher
load_dotenv('.env')
args=json.loads(ARGUMENTS)
async def main():
 a=next(Account.model_validate(x) for x in json.load(open('data/vps/accounts.json')) if x['id']=='podcast-us-youtube');p=Publisher(a)
 with sqlite3.connect('data/vps/queue.sqlite3') as d:
  pending=d.execute("SELECT COUNT(*) FROM jobs WHERE account=? AND state IN ('queued','review','uploading','processing','uncertain','scheduled','held')",(a.id,)).fetchone()[0]
 async with p.client() as c:
  await p.authenticate(c)
  ids=args['ids'] or []
  if not args['ids'] and not pending:
   for channel in args['channels']:
    r=await c.get('https://www.googleapis.com/youtube/v3/search',params={'part':'snippet','type':'video','channelId':channel,'q':args['topic'],'videoLicense':'creativeCommon','order':'date','maxResults':12,'publishedAfter':(datetime.now(timezone.utc)-timedelta(days=14)).isoformat()})
    r.raise_for_status();ids.extend(x['id']['videoId'] for x in r.json().get('items',[]) if x['id'].get('videoId'))
  videos=[]
  if ids:
   r=await c.get('https://www.googleapis.com/youtube/v3/videos',params={'part':'snippet,status,contentDetails','id':','.join(ids[:50])});r.raise_for_status()
   for v in r.json().get('items',[]):
    s=v['snippet'];duration=v['contentDetails']['duration'];parts=re.fullmatch(r'PT(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?',duration)
    seconds=sum(int(x or 0)*n for x,n in zip(parts.groups(),[3600,60,1])) if parts else 0
    if v['status'].get('license')=='creativeCommon' and s['channelId'] in args['channels'] and 90<=seconds<=1800:
     videos.append({'id':v['id'],'title':s['title'],'channel_id':s['channelId'],'author':s['channelTitle'],'published_at':s['publishedAt'],'license':'creativeCommon','seconds':seconds})
  print(json.dumps({'pending':pending,'videos':videos}))
asyncio.run(main())
'''.replace("ARGUMENTS", repr(arguments))
    command = "cd /home/ubuntu/ai-video-clipper && PYTHONPATH=src .venv/bin/python -c " + shlex.quote(code)
    return json.loads(await run("ssh", *ssh_options(worker), worker.host, command, timeout=120))


def eligible(videos, used):
    """Use only fresh, verified, unused videos from the configured original publishers."""
    return [v for v in videos if v["id"] not in used]


async def prepare(cfg, worker, today):
    receipt = cfg.output / (today+".json")
    attempts = 0
    if receipt.exists():
        previous = json.loads(receipt.read_text(encoding="utf-8"))
        attempts = previous.get("attempts", 1)
        if previous["state"] not in {"failed", "processing", "rendered"} or attempts >= 3:
            return previous
    listing = await remote_sources(worker, cfg.channels, cfg.topic)
    if listing["pending"]:
        return {"state":"pending_on_vps","count":listing["pending"]}
    used = set()
    for previous in cfg.output.glob("*.json"):
        item = json.loads(previous.read_text(encoding="utf-8"))
        if item.get("source_id"):
            used.add(item["source_id"])
    choices = eligible(listing["videos"], used)
    if not choices:
        raise ValueError("No new verified CC podcast source; no upload created")
    source = choices[0]
    state = {"state":"processing","source_id":source["id"],"source":source,"date":today,"attempts":attempts+1}
    write_json(receipt, state)  # Interrupted production requires reconciliation, never blind retry.
    output = cfg.output / today
    output.mkdir(exist_ok=True)
    try:
        settings = Settings(whisper_model=cfg.whisper_model, transcription_task="translate",
                            device="cpu", cpu_threads=4, max_clips=1, framing_mode="podcast_panels")
        url = "https://www.youtube.com/watch?v="+source["id"]
        with TemporaryDirectory(prefix="podcast-source-", dir=output) as tmp:
            temp = Path(tmp)
            logger.info("Downloading licensed podcast {}",source["id"])
            media = await download(url, temp)
            logger.info("Transcribing original podcast locally")
            words = await transcribe(media, temp, settings)
            logger.info("Selecting a complete podcast moment")
            moments = await curate(words, settings)
            if not moments:
                raise ValueError("No complete qualifying podcast moment")
            moment = moments[0]
            metadata = await generate(moment, words, settings)
            metadata.description = (metadata.description[:800] +
                f"\nOriginal podcast commentary, uploaded {source['published_at'][:10]}. "
                "Opinions and reported claims belong to the speakers; upload date does not establish event date. "
                "Original audio; English translated subtitles.\n"
                f"Source: {source['title']} by {source['author']}; {url}. "
                "CC BY: https://creativecommons.org/licenses/by/4.0/ . "
                "Changes: excerpt, vertical framing, pause removal and subtitles.")
            await render(media, moment, words, output / "clip.mp4", settings)
            write_json(output / "transcript.json", [w.model_dump() for w in words])
        verified = await remote_sources(worker, cfg.channels, cfg.topic, [source["id"]])
        if not verified["videos"]:
            raise ValueError("Source license changed; delivery withheld")
        write_json(output / "manifest.json", {"metadata":metadata.model_dump(),"source":url,
            "source_id":source["id"],"rights":verified["videos"][0],"moment":moment.model_dump(),
            "format":"original_podcast_clip","published":False,"file":"clip.mp4"})
        state.update(state="rendered",clip=str(output / "clip.mp4"))
        write_json(receipt,state)
        state["state"]="delivering"
        write_json(receipt,state)
        name = await deliver(worker,output / "clip.mp4",output / "manifest.json")
        state.update(state="submitted",outbox_id=name)
        logger.info("Daily podcast delivered: {}",name)
    except BaseException as exc:
        state.update(state="needs_attention" if state["state"]=="delivering" else "failed",error=type(exc).__name__)
        raise
    finally:
        write_json(receipt,state)
    return state


async def watch(cfg, once=False):
    worker=WorkerConfig.model_validate_json(cfg.worker_file.read_text(encoding="utf-8-sig"))
    load_dotenv(worker.env_file)
    cfg.output.mkdir(parents=True,exist_ok=True)
    zone=ZoneInfo(cfg.timezone)
    next_attempt=0
    with worker_lock(cfg.output):
        while True:
            now=datetime.now(zone)
            if once or (now.strftime("%H:%M")>=cfg.prepare_time and asyncio.get_running_loop().time()>=next_attempt):
                try:
                    result=await prepare(cfg,worker,str(now.date()))
                    logger.info("Daily production state: {}",result["state"])
                except Exception as exc:
                    logger.error("Daily production needs attention ({})",type(exc).__name__)
                    if once:
                        raise
                next_attempt=asyncio.get_running_loop().time()+1800
            if once:
                return
            await asyncio.sleep(60)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",required=True,type=Path)
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    cfg=DailyConfig.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
    cfg.output.mkdir(parents=True,exist_ok=True)
    logger.remove()
    logger.add(sys.stderr,backtrace=False,diagnose=False)
    logger.add(cfg.output / "daily-worker.log",rotation="10 MB",retention="14 days",backtrace=False,diagnose=False)
    try:
        asyncio.run(watch(cfg,args.once))
    except Exception as exc:
        logger.error("Worker stopped ({})",type(exc).__name__)
        raise SystemExit(1) from None


if __name__=="__main__":
    main()
