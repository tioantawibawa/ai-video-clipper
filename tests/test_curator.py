import asyncio

from clipper.config import Settings
from clipper.curator import curate
from clipper.models import Word


def test_curator_rejects_bounds_and_overlaps(monkeypatch):
    async def fake(*args):
        return {"moments": [dict(start=a, end=b, hook_score=s, title="Title", reason="Insight", keywords=[])
                            for a, b, s in [(0, 40, 9), (10, 50, 8), (60, 95, 7), (90, 200, 10)]]}
    monkeypatch.setattr("clipper.curator.ask", fake)
    words = [Word(start=i, end=i + .8, text="word") for i in range(100)]
    result = asyncio.run(curate(words, Settings()))
    assert len(result) == 2
    assert result[0].start == 0
    assert result[1].start == 60
