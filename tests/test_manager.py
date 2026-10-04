import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from clipper.cli import app
from clipper.config import Account, Settings
from clipper.content_manager import ContentManager, ads_plan, evaluate
from clipper.manager_config import ApprovedSource, ManagerConfig
from clipper.manager_store import ManagerStore
from clipper.pipeline import Pipeline
from clipper.youtube_insights import metrics


def manager(tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps([Account(id="podcast-us-youtube", platform="youtube", token_env="TOKEN", daily_limit=1).model_dump()]))
    return ContentManager(Pipeline(Settings(data_dir=tmp_path, accounts_file=accounts)), ManagerConfig())


def test_missing_metrics_are_unknown():
    row = metrics({"id": "abc", "snippet": {"title": "x", "channelId": "c", "publishedAt": datetime.now(timezone.utc).isoformat()}})
    assert row["views"] is None and row["engagement_rate"] is None
    assert evaluate([row], None)["videos"][0]["sample_quality"] == "unavailable"


def test_replies_deduplicate_and_crash_becomes_uncertain(tmp_path):
    store = ManagerStore(tmp_path / "store.db")
    store.draft("a", "v", "hello", "Thanks!")
    store.draft("a", "v", "hello", "Changed")
    assert store.claim_reply("a")["text"] == "Thanks!"
    store.recover()
    with pytest.raises(ValueError):
        store.claim_reply("a")
    assert store.replies()[0]["state"] == "uncertain"


def test_unknown_plan_evidence_falls_back(monkeypatch, tmp_path):
    agent = manager(tmp_path)
    monkeypatch.setattr("clipper.content_manager.ask", AsyncMock(return_value={"briefs": [{"evidence_video_id": "invented", "title": "x", "hook": "y", "angle": "z"}], "evaluation": "fake"}))
    result = asyncio.run(agent.plan([{"id": "real", "title": "verified", "url": "https://youtube.com/watch?v=real"}], {}, {}))
    assert result["briefs"][0]["evidence_video_id"] == "real"
    assert "unavailable" in result["evaluation"]


def test_production_only_approved_sources_and_dedup(tmp_path):
    agent = manager(tmp_path)
    agent.config.produce = True
    agent.config.sources = [ApprovedSource(url="https://youtu.be/test123", rights_note="Original video owned by channel", approved=False)]
    assert asyncio.run(agent.produce([]))["state"] == "no_new_approved_sources"
    agent.config.sources[0].approved = True
    assert asyncio.run(agent.produce([]))["state"] == "requested"
    assert asyncio.run(agent.produce([]))["state"] == "no_new_approved_sources"


def test_no_automatic_comment_send(monkeypatch, tmp_path):
    agent = manager(tmp_path)
    agent.api.comments = AsyncMock(return_value=[{"id": "a", "video_id": "v", "text": "Great"}])
    agent.api.reply = AsyncMock()
    monkeypatch.setattr("clipper.content_manager.ask", AsyncMock(return_value={"action": "draft", "text": "Thank you!", "reason": "Ordinary comment"}))
    asyncio.run(agent.draft_comments("channel", [{"id": "v", "title": "video"}], []))
    agent.api.reply.assert_not_called()
    assert agent.store.replies()[0]["state"] == "draft"
    agent.api.reply.return_value = "reply1"
    assert asyncio.run(agent.approve_reply("a")) == "reply1"
    with pytest.raises(ValueError):
        asyncio.run(agent.approve_reply("a"))


def test_daily_run_is_idempotent(tmp_path):
    agent = manager(tmp_path)
    day = str(datetime.now(agent.zone).date())
    expected = agent.root / (day+".json")
    expected.write_text('{}')
    agent.api.trends = AsyncMock()
    assert asyncio.run(agent.run()) == expected
    agent.api.trends.assert_not_called()


def test_ads_never_launch_even_with_budget():
    plan = ads_plan(ManagerConfig(ads_daily_budget_usd=5), {"briefs": []}, [])
    assert plan["planned_budget_usd"] == 35 and plan["launch_enabled"] is False


def test_manager_help():
    result = CliRunner().invoke(app, ["manager", "--help"])
    assert result.exit_code == 0 and "reply-review" in result.output


def test_production_ticket_consumed_once(tmp_path):
    agent = manager(tmp_path)
    agent.config.produce = True
    agent.config.sources = [ApprovedSource(url="https://youtu.be/test123", rights_note="Owned footage approved by owner", approved=True)]
    asyncio.run(agent.produce([]))
    original = Pipeline.process
    Pipeline.process = AsyncMock(return_value=Path("manifest.json"))
    try:
        asyncio.run(agent.pipeline.manager_production_tick())
        asyncio.run(agent.pipeline.manager_production_tick())
        assert Pipeline.process.await_count == 1
        ticket = next((agent.root / "production").glob("*.json"))
        assert json.loads(ticket.read_text())["state"] == "staged"
    finally:
        Pipeline.process = original
