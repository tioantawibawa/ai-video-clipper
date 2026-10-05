from pathlib import Path
import json

import pytest
from pydantic import ValidationError

from clipper.explainer import Episode, ass_time, subtitles
from clipper.models import Word


def test_word_captions_escape_override_injection(tmp_path):
    path = tmp_path / "captions.ass"
    subtitles([Word(start=0, end=.3, text=r"{\pos(0,0)}Hello")], path, 1080, 1920)
    text = path.read_text()
    assert r"{\pos" not in text
    assert "0:00:00.30" in text
    assert ass_time(59.999) == "0:01:00.00"


def test_dated_plans_and_short_duration_word_budget():
    root = Path(__file__).parents[1] / "content/ronaldo-portugal-2026-10-05"
    for path in root.glob("*.json"):
        episode = Episode.model_validate_json(path.read_text())
        assert len(episode.sources) == 2
        if episode.vertical:
            assert sum(len(s.narration.split()) for s in episode.scenes) < 90
        else:
            assert len(episode.scenes) >= 15


def test_invalid_visual_mode_is_rejected():
    with pytest.raises(ValidationError):
        from clipper.explainer import Scene
        Scene(heading="Test", points=["Test"], narration="This is long enough to validate.", mode="fake-match")
