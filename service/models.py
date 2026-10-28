from typing import Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Prompt = Annotated[str, Field(min_length=3, max_length=6000)]
Reference = Annotated[str, Field(max_length=1_500_000)]

class JobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    prompts: list[Prompt] = Field(min_length=1, max_length=50)
    references: list[Reference] = Field(default_factory=list, max_length=4)
    width: int = 768
    height: int = 768
    seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    max_run_seconds: float = Field(default=300, ge=1, le=3600, allow_inf_nan=False)

    @field_validator('prompts')
    @classmethod
    def nonempty_prompts(cls, values):
        if any(len(v.strip()) < 3 for v in values):
            raise ValueError('Each prompt must contain at least three non-whitespace characters.')
        return values

    @model_validator(mode='after')
    def dimensions(self):
        if (self.width, self.height) not in {(768, 768), (1024, 768), (768, 1024), (1024, 1024)}:
            raise ValueError('Supported sizes: 768x768, 1024x768, 768x1024 and 1024x1024.')
        return self
