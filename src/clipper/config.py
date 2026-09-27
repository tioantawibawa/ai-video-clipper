import json
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLIPPER_", env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    accounts_file: Path = Path("accounts.json")
    sources_file: Path = Path("sources.json")
    llm_provider: Literal["openai", "anthropic", "gemini"] = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    transcription: Literal["local", "api"] = "local"
    whisper_model: str = "small.en"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    cpu_threads: int = Field(4, ge=1, le=64)
    min_duration: float = Field(30, ge=1)
    max_duration: float = Field(60, le=60)
    max_clips: int = Field(3, ge=1, le=20)
    width: int = 1080
    height: int = 1920
    font: str = Field("DejaVu Sans", pattern=r"^[\w -]+$")
    font_size: int = Field(68, ge=10, le=120)
    highlight: str = Field("FFFF00", pattern=r"^[0-9A-Fa-f]{6}$")
    review: bool = True
    poll_seconds: int = Field(900, ge=30)
    subprocess_timeout: int = 7200

    @model_validator(mode="after")
    def valid_output(self):
        if not 0 < self.min_duration <= self.max_duration:
            raise ValueError("Invalid duration range")
        if self.width <= 0 or self.height <= 0 or self.width * 16 != self.height * 9:
            raise ValueError("Output must be 9:16")
        if self.width % 2 or self.height % 2:
            raise ValueError("Output dimensions must be even")
        return self


class Account(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    platform: Literal["youtube", "tiktok", "instagram"]
    token_env: str
    proxy_env: str | None = None
    warmed: bool = False
    daily_limit: int = Field(3, ge=1, le=4)
    timezone: str = "America/New_York"
    privacy: str = "private"
    user_id: str = ""
    graph_version: str = "v23.0"
    public_media_base: str = ""

    @model_validator(mode="after")
    def valid_zone(self):
        ZoneInfo(self.timezone)
        return self


class Source(BaseModel):
    url: str
    kind: Literal["channel", "rss"] = "channel"
    proxy_env: str | None = None
    accounts: list[str] = []
    limit: int = Field(5, ge=1, le=50)


def load_items(path: Path, model):
    return [model.model_validate(x) for x in json.loads(path.read_text())] if path.exists() else []
