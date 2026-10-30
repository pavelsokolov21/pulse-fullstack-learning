from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from pulse.domain.types import DEFAULT_TIMEOUT, Interval, Timeout, Url


class MonitorConfig(BaseModel):
    """Монитор из файла конфигурации."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: Url
    interval: Interval
    timeout: Timeout = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class Monitor:
    """Сохранённый монитор. Значения уже проверены, поэтому класс «глупый»."""

    id: int
    url: str
    interval: timedelta
    timeout: float
