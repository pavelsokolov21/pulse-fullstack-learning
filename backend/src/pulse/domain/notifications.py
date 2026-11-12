from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class FailureNotification:
    """Что отправляем, когда проверка монитора не прошла."""

    check_id: int
    monitor_id: int
    url: str
    status_code: int | None
    error: str | None
    checked_at: datetime

    @property
    def reason(self) -> str:
        if self.error is not None:
            return self.error
        return f"HTTP {self.status_code}"


class DeliveryOutcome(StrEnum):
    SENT = "sent"
    DUPLICATE = "duplicate"  # уже отправляли: повторная доставка задачи
    SKIPPED = "skipped"  # проверки больше нет или она успешна: сообщать не о чем


class TaskState(StrEnum):
    """Шаг жизни задачи «уведомить о сбое» для журнала и страницы с задачами."""

    QUEUED = "queued"  # API положил задачу в очередь
    STARTED = "started"  # воркер начал попытку
    RETRY = "retry"  # попытка не удалась, воркер запланировал следующую
    SENT = "sent"
    DUPLICATE = "duplicate"
    SKIPPED = "skipped"
    FAILED = "failed"  # окончательно: повторы кончились или повтор не поможет


@dataclass(frozen=True)
class TaskEvent:
    """Запись журнала задач. `retry_in`: через сколько секунд следующая попытка."""

    check_id: int
    state: TaskState
    at: datetime
    attempt: int | None = None
    detail: str | None = None
    retry_in: float | None = None


class RetryableDeliveryError(Exception):
    """Доставка не удалась по причине, которая может пройти: повтор имеет смысл.

    Сеть, таймаут, 5xx, 429. `retry_after`: сколько секунд просит подождать сервер
    (заголовок Retry-After), `None`, если не просил.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentDeliveryError(Exception):
    """Доставка не удастся и при повторе (неверный адрес, отказ 4xx): не повторять."""
