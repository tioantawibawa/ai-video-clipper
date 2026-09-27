import math

import pytest

from clipper.config import Settings
from clipper.editor import ass_time, keep_intervals, remap_words, subtitles
from clipper.models import Word


def test_silence_cut_remaps_audio_video_and_subtitles():
    intervals = keep_intervals("silence_start: 2\nsilence_end: 4", 6)
    assert intervals == [(0, 2.08), (3.92, 6)]
    words = [Word(start=10.5, end=11, text="Hello"), Word(start=14.5, end=15, text="again")]
    mapped = remap_words(words, 10, intervals)
    assert mapped[0].start == .5
    assert mapped[1].start == pytest.approx(2.66)
    assert mapped[1].end == pytest.approx(3.16)


def test_trailing_silence_and_no_silence():
    assert keep_intervals("", 5) == [(0, 5)]
    assert keep_intervals("silence_start: 2", 5) == [(0, 2.08), (4.92, 5)]


def test_subtitles_escape_injection_and_highlight(tmp_path):
    target = tmp_path / "captions.ass"
    subtitles([Word(start=0, end=1, text=r"{\pos(0,0)}HELLO")], target, Settings())
    text = target.read_text()
    assert r"\pos" not in text
    assert r"\c&H00FFFF&" in text
    assert ass_time(59.999) == "0:01:00.00"


def test_invalid_timestamps_rejected():
    for start, end in [(2, 1), (0, math.inf), (math.nan, 1)]:
        with pytest.raises(ValueError):
            Word(start=start, end=end, text="bad")
