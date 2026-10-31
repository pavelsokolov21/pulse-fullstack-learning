from collections.abc import Callable
from datetime import UTC, datetime

# Часы передаются в сервисы параметром, чтобы тесты подставляли своё время.
type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)
