from datetime import datetime

from pydantic import BaseModel, ConfigDict

from pulse.domain.notifications import TaskState


class TaskEventRead(BaseModel):
    """Событие фоновой задачи «уведомить о сбое» для страницы с задачами."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    check_id: int
    state: TaskState
    at: datetime
    attempt: int | None
    detail: str | None
    # Через сколько секунд следующая попытка (только у состояния `retry`).
    retry_in: float | None
