"""Editorial policy, independent from rendering and publishing credentials."""
from pathlib import Path
from pydantic import BaseModel, Field, model_validator
from .downloader import canonical_video


class ApprovedSource(BaseModel):
    url: str
    rights_note: str = Field(min_length=10)
    approved: bool = False

    @model_validator(mode="after")
    def canonical(self):
        self.url = canonical_video(self.url)
        return self


class ManagerConfig(BaseModel):
    account: str = "podcast-us-youtube"
    topics: list[str] = Field(default_factory=lambda: ["Cristiano Ronaldo", "Manchester United"], min_length=1, max_length=5)
    region: str = Field("US", pattern="^[A-Z]{2}$")
    language: str = "en"
    lookback_days: int = Field(7, ge=1, le=30)
    plan_days: int = Field(7, ge=1, le=14)
    owned_video_limit: int = Field(30, ge=1, le=200)
    comments_per_video: int = Field(20, ge=1, le=100)
    max_reply_drafts: int = Field(10, ge=0, le=30)
    produce: bool = False
    sources: list[ApprovedSource] = Field(default_factory=list)
    video_review: bool = True
    ads_daily_budget_usd: float | None = Field(None, gt=0, le=10000)
    ads_duration_days: int = Field(7, ge=1, le=30)

    @classmethod
    def load(cls, path: Path):
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
