"""Идентификатор текущего запроса в контексте выполнения и в логах.

Не зависит от HTTP: ту же переменную могут выставлять воркер или CLI, чтобы
записи лога одной операции можно было связать.
"""

import logging
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Id текущего запроса или `None` вне запроса."""
    return request_id_var.get()


def new_request_id() -> str:
    return uuid.uuid4().hex


class RequestIdFilter(logging.Filter):
    """Добавляет в запись лога поле `request_id` (для формата `%(request_id)s`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Явно переданный через `extra=` id важнее: он нужен там, где контекст
        # запроса уже сброшен.
        if "request_id" not in record.__dict__:
            record.__dict__["request_id"] = get_request_id() or "-"
        return True
