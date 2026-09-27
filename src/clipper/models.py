from pydantic import BaseModel, ConfigDict, Field, model_validator


class Word(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("End must follow start")
        return self


class Moment(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str
    hook_score: int = Field(ge=0, le=10)
    reason: str
    keywords: list[str]


class Metadata(BaseModel):
    title: str = Field(min_length=1, max_length=59)
    description: str = Field(max_length=2000)
    hashtags: list[str] = Field(max_length=8)
