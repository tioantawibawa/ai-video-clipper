"""Editorial policy, independent from rendering and publishing credentials."""
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from .downloader import canonical_video
from .source_policy import SpeakingSource


class ApprovedSource(BaseModel):
    url: str
    rights_note: str = Field(min_length=10)
    approved: bool = False
    attribution: str = Field("", max_length=1000)

    @model_validator(mode="after")
    def canonical(self):
        self.url = canonical_video(self.url)
        return self


class ManagerConfig(BaseModel):
    source_format: Literal["any", "ronaldo_speaking"] = "any"
    speaking_sources: list[SpeakingSource] = Field(default_factory=list)
    account: str = "podcast-us-youtube"
    research_scope: Literal["topics", "all"] = "topics"
    topics: list[str] = Field(default_factory=lambda: ["Cristiano Ronaldo", "Manchester United"], min_length=1, max_length=5)
    region: str = Field("US", pattern="^[A-Z]{2}$")
    language: str = "en"
    lookback_days: int = Field(7, ge=1, le=30)
    plan_days: int = Field(7, ge=1, le=14)
    owned_video_limit: int = Field(30, ge=1, le=200)
    comments_per_video: int = Field(20, ge=1, le=100)
    max_reply_drafts: int = Field(10, ge=0, le=30)
    reply_mode: Literal["draft", "auto"] = "draft"
    produce: bool = False
    auto_cc_sources: bool = False
    sources: list[ApprovedSource] = Field(default_factory=list)
    video_review: bool = True
    ads_daily_budget_usd: float | None = Field(None, gt=0, le=10000)
    ads_duration_days: int = Field(7, ge=1, le=30)

    @classmethod
    def load(cls, path: Path):
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
