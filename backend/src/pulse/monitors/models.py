from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pulse.utils.normalize_url import normalize_url
from pulse.utils.parse_interval import parse_interval


class MonitorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    interval: timedelta
    timeout: float = Field(default=10.0, gt=0)

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        return normalize_url(value)

    @field_validator("interval", mode="before")
    @classmethod
    def _parse_interval(cls, value: object) -> object:
        if isinstance(value, str):
            return parse_interval(value)
        return value
