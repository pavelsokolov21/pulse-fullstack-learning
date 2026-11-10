from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, model_validator

from pulse.domain.checks import Check
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


@dataclass(frozen=True)
class MonitorWithOwner(Monitor):
    """Монитор для списка: вместе с email владельца (в таблице нужен он, а не id)."""

    owner_email: str


@dataclass(frozen=True)
class MonitorWithLastCheck(MonitorWithOwner):
    """Монитор для списка и его последняя проверка (`None`, если проверок ещё нет)."""

    last_check: Check | None

    @classmethod
    def of(
        cls, monitor: MonitorWithOwner, last_check: Check | None
    ) -> "MonitorWithLastCheck":
        return cls(
            id=monitor.id,
            owner_id=monitor.owner_id,
            url=monitor.url,
            interval=monitor.interval,
            timeout=monitor.timeout,
            owner_email=monitor.owner_email,
            last_check=last_check,
        )
