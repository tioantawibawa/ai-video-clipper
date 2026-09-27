import asyncio
import json
import shutil
import sys

import typer
from dotenv import load_dotenv
from loguru import logger

from .config import Settings
from .pipeline import Pipeline, worker_lock
from .scheduler import utc_label

app = typer.Typer(no_args_is_help=True, help="Headless podcast clipping and reviewed publishing")


def pipeline():
    load_dotenv()
    cfg = Settings()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level="INFO", backtrace=False, diagnose=False)
    logger.add(cfg.data_dir / "agent.log", rotation="10 MB", retention="14 days",
               backtrace=False, diagnose=False, enqueue=True)
    return Pipeline(cfg)


def execute(coroutine):
    try:
        return asyncio.run(coroutine)
    except KeyboardInterrupt:
        raise typer.Exit(130)
    except Exception as exc:
        logger.error("Operation failed: {}. Check configuration and provider access.", type(exc).__name__)
        raise typer.Exit(1) from None


@app.command()
def process(url: str, account: list[str] = typer.Option([], "--account")):
    """Stage clips from one URL; optionally queue to named accounts."""
    agent = pipeline()
    with worker_lock(agent.root):
        manifest = execute(agent.process(url, account))
    typer.echo(str(manifest))


@app.command()
def daemon():
    """Monitor sources, process episodes, and service the publish queue."""
    agent = pipeline()
    with worker_lock(agent.root):
        execute(agent.daemon())


@app.command()
def queue():
    """List durable job states and reserved times."""
    for row in pipeline().queue.rows():
        typer.echo(f"{row['id']} {row['account']} {row['state']} {utc_label(row['due'])} {row['clip']}")


@app.command()
def review(job_id: int, approve: bool = typer.Option(False, "--approve/--reject")):
    """Approve or reject a staged job after viewing its media and manifest."""
    agent = pipeline()
    row = next((r for r in agent.queue.rows() if r["id"] == job_id), None)
    if not row:
        raise typer.BadParameter("Unknown job")
    agent.queue.review(job_id, approve, agent.accounts[row["account"]])
    typer.echo("Approved" if approve else "Rejected")


@app.command()
def reconcile(job_id: int, outcome: str, remote_id: str = ""):
    """Resolve uncertain uploads AFTER checking the platform; outcome published/failed/processing."""
    if outcome not in {"published", "failed", "processing"}:
        raise typer.BadParameter("Use published, failed, or processing")
    agent = pipeline()
    with worker_lock(agent.root):
        row = next((r for r in agent.queue.rows() if r["id"] == job_id), None)
        if not row or row["state"] not in {"uncertain", "uploading"}:
            raise typer.BadParameter("Job must have an uncertain upload outcome")
        if outcome in {"published", "processing"} and not (remote_id or row["remote_id"]):
            raise typer.BadParameter("A verified remote id is required")
        agent.queue.set_state(job_id, outcome, remote_id or None)


@app.command()
def doctor():
    """Check local executables; does not upload or contact providers."""
    result = {tool: shutil.which(tool) for tool in ("ffmpeg", "ffprobe")}
    typer.echo(json.dumps(result, indent=2))
    if not all(result.values()):
        raise typer.Exit(1)
