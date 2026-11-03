from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, model_validator

from pulse.domain.types import DEFAULT_TIMEOUT, Interval, Timeout, Url


def ensure_timeout_fits_interval(interval: timedelta, timeout: float) -> None:
    """Таймаут короче интервала, иначе проверки монитора накладываются друг на друга.

    Тот же запрет в БД: CHECK monitors_timeout_below_interval (db/schema.sql).
    """
    if timeout >= interval.total_seconds():
        raise ValueError("таймаут должен быть короче интервала проверки")


class MonitorConfig(BaseModel):
    """Монитор из файла конфигурации."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: Url
    interval: Interval
    timeout: Timeout = DEFAULT_TIMEOUT

    @model_validator(mode="after")
    def _timeout_fits_interval(self) -> "MonitorConfig":
        ensure_timeout_fits_interval(self.interval, self.timeout)
        return self


@dataclass(frozen=True)
class Monitor:
    """Сохранённый монитор. Значения уже проверены, поэтому класс «глупый»."""

    id: int
    owner_id: int
    url: str
    interval: timedelta
    timeout: float
