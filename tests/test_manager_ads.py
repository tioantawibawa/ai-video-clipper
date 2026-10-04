import asyncio
from unittest.mock import AsyncMock

from clipper.content_manager import ContentManager
from test_manager import manager


def test_ads_creative_schema_and_no_launch(monkeypatch, tmp_path):
    agent = manager(tmp_path)
    monkeypatch.setattr("clipper.content_manager.ask", AsyncMock(return_value={"creatives": [{
        "headline": "Football conversations that matter", "description": "Independent fan discussion of Ronaldo and Manchester United.",
        "video_script": "Explore the stories behind the football conversation. Watch our independent fan channel.", "call_to_action": "Watch now"}]}))
    errors = []
    draft = asyncio.run(agent.prepare_ads({"briefs": [{"title": "Football"}]}, [], errors))
    assert draft["creatives"][0]["call_to_action"] == "Watch now"
    assert draft["launch_enabled"] is False and not errors


def test_oversized_ad_copy_is_not_accepted(monkeypatch, tmp_path):
    agent = manager(tmp_path)
    monkeypatch.setattr("clipper.content_manager.ask", AsyncMock(return_value={"creatives": [{
        "headline": "x"*60, "description": "text", "video_script": "script", "call_to_action": "Watch"}]}))
    errors = []
    draft = asyncio.run(agent.prepare_ads({"briefs": [{"title": "Football"}]}, [], errors))
    assert not draft["creatives"] and errors[0]["component"] == "ads_creative"


def test_readable_report(tmp_path):
    path = tmp_path / "report.md"
    ContentManager.write_report(path, {"generated_at": "now", "topics": ["Ronaldo"],
        "plan": {"briefs": [], "evaluation": "No data"}, "retention": {"available": False},
        "ads": {"daily_budget_usd": None}, "production": {"enabled": False}, "reply_drafts": [], "errors": []})
    assert "Campaign is not launched" in path.read_text()
