"""Демонстрация конкурентности: два параллельных вызова на одну строку.

Запускает НАСТОЯЩУЮ гонку на настоящей базе и записывает, что происходило и когда,
чтобы это можно было нарисовать. Сервисный код (`IncidentService`) здесь тот же, что
в бою; события собирает подкласс репозитория, а не правка сервиса.

Только для разработки: небезопасный вариант (`Strategy.UNSAFE`) теряет обновления
намеренно, а прогон создаёт и удаляет временный монитор. В `prod` ручка выключена.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pulse.domain.concurrency_demo import (
    CallResult,
    DemoRun,
    EventKind,
    Strategy,
    TraceEvent,
)
from pulse.domain.errors import ConcurrentUpdateError
from pulse.domain.incidents import Incident
from pulse.repositories.database import Session, SessionFactory
from pulse.repositories.incidents import IncidentRepository
from pulse.repositories.monitors import MonitorRepository
from pulse.services.incidents import IncidentService
from pulse.services.retry import retry_on_conflict

INITIAL = 3
RUN_TIMEOUT = 30  # секунд: страховка, чтобы зависшая блокировка не держала запрос


class _Trace:
    """Журнал событий прогона с общим отсчётом времени."""

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self.events: list[TraceEvent] = []

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._started) * 1000, 1)

    def add(self, call: int, attempt: int, kind: EventKind, value: int | None) -> None:
        self.events.append(TraceEvent(call, attempt, self.elapsed_ms(), kind, value))


class _TracedIncidentRepository(IncidentRepository):
    """Обычный репозиторий, который ещё и записывает, что и когда он сделал."""

    def __init__(
        self, session: Session, trace: _Trace, call: int, attempt: int
    ) -> None:
        super().__init__(session)
        self._trace = trace
        self._call = call
        self._attempt = attempt

    def _note(self, kind: EventKind, value: int | None = None) -> None:
        self._trace.add(self._call, self._attempt, kind, value)

    def start(self) -> None:
        self._note(EventKind.START)

    async def get(self, incident_id: int) -> Incident:
        incident = await super().get(incident_id)
        self._note(EventKind.READ, incident.failed_checks)
        return incident

    async def get_for_update(self, incident_id: int) -> Incident:
        self._note(EventKind.LOCK_REQUESTED)
        incident = await super().get_for_update(incident_id)
        self._note(EventKind.READ, incident.failed_checks)
        return incident

    async def set_failed_checks(self, incident_id: int, value: int) -> None:
        await super().set_failed_checks(incident_id, value)
        self._note(EventKind.WRITE, value)

    async def set_failed_checks_if_version(
        self, incident_id: int, value: int, expected_version: int
    ) -> None:
        try:
            await super().set_failed_checks_if_version(
                incident_id, value, expected_version
            )
        except ConcurrentUpdateError:
            self._note(EventKind.CONFLICT)
            raise
        self._note(EventKind.WRITE, value)

    async def commit(self) -> None:
        await super().commit()
        self._note(EventKind.COMMIT)


class ConcurrencyDemoService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._factory = session_factory

    async def run(self, owner_id: int, strategy: Strategy, hold: timedelta) -> DemoRun:
        """Два параллельных вызова на одном инциденте; `hold` держит их между
        чтением и записью, чтобы окно гонки было видно глазами."""
        monitor_id, incident_id = await self._prepare(owner_id)
        try:
            trace = _Trace()
            async with asyncio.timeout(RUN_TIMEOUT):
                calls = await asyncio.gather(
                    self._call(1, strategy, incident_id, hold, trace),
                    self._call(2, strategy, incident_id, hold, trace),
                )
            elapsed_ms = trace.elapsed_ms()
            final, version = await self._read_final(incident_id)
        finally:
            await self._cleanup(monitor_id)
        return DemoRun(
            strategy=strategy,
            hold_ms=int(hold.total_seconds() * 1000),
            initial=INITIAL,
            expected=INITIAL + 2,
            final=final,
            final_version=version,
            elapsed_ms=elapsed_ms,
            events=tuple(sorted(trace.events, key=lambda e: e.at_ms)),
            calls=tuple(calls),
        )

    async def _call(
        self,
        call: int,
        strategy: Strategy,
        incident_id: int,
        hold: timedelta,
        trace: _Trace,
    ) -> CallResult:
        attempts = 0

        async def attempt() -> int:
            nonlocal attempts
            attempts += 1
            return await self._attempt(
                call, attempts, strategy, incident_id, hold, trace
            )

        try:
            if strategy is Strategy.VERSION:
                # Повтор в вызывающем коде: каждая попытка открывает свою сессию.
                returned = await retry_on_conflict(attempt)
            else:
                returned = await attempt()
        except ConcurrentUpdateError as error:
            return CallResult(call, None, attempts, str(error))
        return CallResult(call, returned, attempts, None)

    async def _attempt(
        self,
        call: int,
        attempt: int,
        strategy: Strategy,
        incident_id: int,
        hold: timedelta,
        trace: _Trace,
    ) -> int:
        async with self._factory() as session:
            repository = _TracedIncidentRepository(session, trace, call, attempt)
            repository.start()

            async def pause() -> None:
                await asyncio.sleep(hold.total_seconds())

            match strategy:
                case Strategy.UNSAFE:
                    return await _register_unsafe(repository, incident_id, pause)
                case Strategy.LOCK:
                    service = IncidentService(repository, before_write=pause)
                    return await service.register_failed_check(incident_id)
                case Strategy.VERSION:
                    service = IncidentService(repository, before_write=pause)
                    return await service.register_failed_check_optimistic(incident_id)

    async def _prepare(self, owner_id: int) -> tuple[int, int]:
        """Временный монитор и инцидент: настоящие, закоммиченные строки."""
        async with self._factory() as session:
            monitor = await MonitorRepository(session).create(
                owner_id=owner_id,
                url=f"https://concurrency-demo-{uuid.uuid4().hex}.invalid",
                interval=timedelta(minutes=5),
                timeout=5,
            )
            incidents = IncidentRepository(session)
            incident = await incidents.create(
                monitor_id=monitor.id,
                started_at=datetime.now(UTC),
                failed_checks=INITIAL,
            )
            await incidents.commit()  # одна сессия на оба репозитория
        return monitor.id, incident.id

    async def _read_final(self, incident_id: int) -> tuple[int, int]:
        async with self._factory() as session:
            incident = await IncidentRepository(session).get(incident_id)
        return incident.failed_checks, incident.version

    async def _cleanup(self, monitor_id: int) -> None:
        """Удаляет монитор; инцидент уходит каскадом."""
        async with self._factory() as session:
            await MonitorRepository(session).delete(monitor_id)
            await IncidentRepository(session).commit()


async def _register_unsafe(
    repository: IncidentRepository,
    incident_id: int,
    pause: Callable[[], Awaitable[None]],
) -> int:
    # DEMO-ONLY: намеренная гонка (lost update). Боевого аналога в сервисе нет.
    incident = await repository.get(incident_id)
    new_value = incident.failed_checks + 1
    await pause()
    await repository.set_failed_checks(incident_id, new_value)
    await repository.commit()
    return new_value
