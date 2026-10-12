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
