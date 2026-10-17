from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    url: str
    status_code: int | None
    latency_ms: float
    error: str | None

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code <= 399


@dataclass(frozen=True)
class HostMeta:
    total: int
    failed: int


@dataclass(frozen=True)
class HostStats:
    host: str
    total: int
    failed: int
    failure_rate: float
    # None, если у хоста не было ни одного ответа: 0.0 прочитался бы как «самый
    # быстрый сайт», хотя данных нет.
    p95: float | None
