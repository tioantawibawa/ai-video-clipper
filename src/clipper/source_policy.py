"""Human-verified sources: Ronaldo is the speaker, not a discussion topic."""
from pydantic import BaseModel, Field, model_validator


class SpeakingSource(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    verified_note: str = Field(min_length=20)
    portrait_crop: tuple[int, int, int, int] | None = None
    archive_year: int | None = Field(None, ge=2000, le=2100)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end - self.start < 30:
            raise ValueError("Verified speaking interval must contain at least 30 seconds")
        if self.portrait_crop:
            w, h, x, y = self.portrait_crop
            if min(w, h) <= 0 or min(x, y) < 0:
                raise ValueError('Invalid source crop')
        return self

    def frame_filter(self, cfg):
        if not self.portrait_crop:
            return None
        w, h, x, y = self.portrait_crop
        result = (f'crop={w}:{h}:{x}:{y},scale={cfg.width}:{cfg.height-400}:'
                f'force_original_aspect_ratio=decrease,pad={cfg.width}:{cfg.height}:'
                '(ow-iw)/2:120:color=black,setsar=1')
        return result


def speaking_words(words, source):
    return [w for w in words if w.start >= source.start and w.end <= source.end]


def verified_moments(moments, source):
    return [m for m in moments if source.start <= m.start < m.end <= source.end]
