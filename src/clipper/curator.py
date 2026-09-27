from .config import Settings
from .llm import ask
from .models import Moment, Word


def chunks(words: list[Word], window: float = 300, overlap: float = 60):
    cursor = words[0].start
    while cursor < words[-1].end:
        selected = [w for w in words if cursor <= w.start < cursor + window]
        if selected:
            yield selected
        cursor += window - overlap


async def curate(words: list[Word], cfg: Settings) -> list[Moment]:
    candidates = []
    for chunk in chunks(words):
        data = await ask(cfg,
            f"Select complete {cfg.min_duration}-{cfg.max_duration} second podcast moments. "
            "Score hook strength in first 3 seconds (0-10), emotion/humor/debate/actionable insight. "
            "Preserve context; avoid misleading edits. Use exact supplied absolute word boundaries. "
            "Return {moments:[{start:number,end:number,title:string,hook_score:integer,"
            "reason:string,keywords:[string]}]}. Return empty moments if none qualify.",
            [w.model_dump() for w in chunk])
        for item in data.get("moments", []):
            moment = Moment.model_validate(item)
            if not chunk[0].start <= moment.start < moment.end <= chunk[-1].end:
                continue
            # Snap hallucinated/subword boundaries to transcript boundaries.
            moment.start = min(chunk, key=lambda w: abs(w.start - moment.start)).start
            moment.end = min(chunk, key=lambda w: abs(w.end - moment.end)).end
            if cfg.min_duration <= moment.end - moment.start <= cfg.max_duration:
                candidates.append(moment)
    selected = []
    for moment in sorted(candidates, key=lambda m: m.hook_score, reverse=True):
        if all(min(moment.end, x.end) <= max(moment.start, x.start) for x in selected):
            selected.append(moment)
        if len(selected) == cfg.max_clips:
            break
    return selected
