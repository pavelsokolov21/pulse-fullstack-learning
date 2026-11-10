from dataclasses import dataclass
from enum import StrEnum


class Strategy(StrEnum):
    """Как два параллельных вызова обновляют один счётчик."""

    UNSAFE = "unsafe"  # прочитать, прибавить, записать: без защиты
    LOCK = "lock"  # SELECT ... FOR UPDATE: второй ждёт первого
    VERSION = "version"  # версия строки: проигравший повторяет попытку


class EventKind(StrEnum):
    START = "start"  # попытка началась: открыта своя сессия
    LOCK_REQUESTED = "lock_requested"  # запрошена блокировка строки (возможно, ждём)
    READ = "read"  # счётчик прочитан (в `value`)
    WRITE = "write"  # новое значение записано в транзакции (в `value`)
    COMMIT = "commit"  # транзакция зафиксирована, блокировка снята
    CONFLICT = "conflict"  # версия строки устарела, запись отклонена


@dataclass(frozen=True)
class TraceEvent:
    """Одно событие в жизни вызова. `at_ms` отсчитывается от начала прогона."""

    call: int
    attempt: int
    at_ms: float
    kind: EventKind
    value: int | None


@dataclass(frozen=True)
class CallResult:
    call: int
    returned: int | None  # новое значение счётчика или `None`, если вызов не удался
    attempts: int
    error: str | None


@dataclass(frozen=True)
class DemoRun:
    """Итог одного прогона: что ожидали, что получили и как это происходило."""

    strategy: Strategy
    hold_ms: int
    initial: int
    expected: int
    final: int
    final_version: int
    elapsed_ms: float
    events: tuple[TraceEvent, ...]
    calls: tuple[CallResult, ...]

    @property
    def lost_update(self) -> bool:
        return self.final != self.expected
