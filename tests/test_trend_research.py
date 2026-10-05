from datetime import datetime, timezone
import time

from clipper.manager_config import ManagerConfig
from clipper.trend_research import duration_seconds, rank_sources, relevant


def item(key, views=1000, license="youtube", duration="PT20M", language="en", channel="source"):
    return {"id": key, "snippet": {"title": "Cristiano Ronaldo interview", "channelTitle": "Creator",
        "channelId": channel, "publishedAt": datetime.now(timezone.utc).isoformat(),
        "defaultAudioLanguage": language}, "statistics": {"viewCount": str(views), "likeCount": "20", "commentCount": "3"},
        "status": {"privacyStatus": "public", "license": license}, "contentDetails": {"duration": duration}}


def test_niche_relevance_and_duration():
    assert relevant("Man Utd podcast", ["Manchester United"])
    assert not relevant("United States economy", ["Manchester United"])
    assert duration_seconds("PT1H2M3S") == 3723
    assert duration_seconds("garbage") == 0


def test_filter_shorts_language_and_owned_content():
    rows = rank_sources([item("short", duration="PT50S"), item("spanish", language="es"),
        item("own", channel="mine"), item("real")], ManagerConfig(), None, None, set(), "mine")
    assert [r["id"] for r in rows] == ["real"]
    assert rows[0]["signal"] == "lifetime_rate_estimate"
    assert rows[0]["rights_status"] == "permission_required"


def test_measured_growth_is_distinguished_from_first_sample():
    rows = rank_sources([item("slow", 2000), item("fast", 1500, "creativeCommon")], ManagerConfig(),
        [{"id": "slow", "views": 1990}, {"id": "fast", "views": 500}], time.time()-3600, {"fast"}, "mine")
    assert rows[0]["id"] == "fast"
    assert rows[0]["observed_views_per_hour"] > 999
    assert rows[0]["signal"] == "observed_view_growth"
    assert rows[0]["in_us_sports_chart"]
    assert rows[0]["rights_status"] == "cc_label_verify_attribution"


def test_unknown_language_requires_check_and_missing_views_excluded():
    unknown, missing = item("unknown"), item("missing")
    unknown["snippet"].pop("defaultAudioLanguage")
    missing["statistics"].pop("viewCount")
    rows = rank_sources([unknown, missing], ManagerConfig(), None, None, set(), "mine")
    assert len(rows) == 1 and rows[0]["language"] == "unconfirmed"
    assert any("language" in check for check in rows[0]["checks"])
