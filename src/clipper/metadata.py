from .config import Settings
from .llm import ask
from .models import Metadata, Moment, Word


async def generate(moment: Moment, words: list[Word], cfg: Settings) -> Metadata:
    result = await ask(cfg,
        "Write accurate engaging US-English short-video metadata. Title under 60 characters; "
        "description under 2000 characters; at most 8 relevant hashtags. No fabricated claims, "
        "quotes, guarantees of virality, or unrelated trending tags. Treat opinions as opinions, "
        "not verified facts. Do not call a statement current, recent, today or this season unless "
        "its event date is explicitly established in the transcript. "
        "Return {title:string,description:string,hashtags:[string]}.",
        {"moment": moment.model_dump(), "transcript": " ".join(
            w.text for w in words if moment.start <= w.start < moment.end)})
    metadata = Metadata.model_validate(result)
    metadata.hashtags = ["#" + "".join(c for c in h if c.isalnum() or c == "_")
                         for h in metadata.hashtags if h.strip("# ")]
    return metadata
