"""Прогон демонстрации конкурентности: настоящая гонка, записанная по событиям."""

from collections import defaultdict
from datetime import timedelta

import psycopg
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.domain.concurrency_demo import DemoRun, EventKind, Strategy, TraceEvent
from pulse.repositories.database import SessionFactory, create_session_factory
from pulse.services.concurrency_demo import ConcurrencyDemoService

Db = psycopg.Connection[tuple[object, ...]]
HOLD = timedelta(milliseconds=150)

pytestmark = pytest.mark.usefixtures("clean_database")


@pytest.fixture
def factory(engine: AsyncEngine) -> SessionFactory:
    return create_session_factory(engine)


@pytest.fixture
def owner_id(admin_db: Db) -> int:
    row = admin_db.execute(
        "INSERT INTO users (email, password_hash, role)"
        " VALUES ('owner@example.com', 'h', 'manager') RETURNING id"
    ).fetchone()
    assert row is not None
    return int(row[0])  # pyright: ignore[reportArgumentType]


async def run(factory: SessionFactory, owner_id: int, strategy: Strategy) -> DemoRun:
    return await ConcurrencyDemoService(factory).run(owner_id, strategy, HOLD)


def kinds(run: DemoRun, call: int) -> list[EventKind]:
    return [e.kind for e in run.events if e.call == call]


def first(run: DemoRun, call: int, kind: EventKind) -> TraceEvent:
    return next(e for e in run.events if e.call == call and e.kind is kind)


async def test_unsafe_loses_one_update(factory: SessionFactory, owner_id: int) -> None:
    result = await run(factory, owner_id, Strategy.UNSAFE)

    assert (result.initial, result.expected, result.final) == (3, 5, 4)
    assert result.lost_update
    assert [c.returned for c in result.calls] == [4, 4]
    # Оба прочитали до того, как любой записал: в этом и есть окно гонки.
    last_read = max(first(result, c, EventKind.READ).at_ms for c in (1, 2))
    first_write = min(first(result, c, EventKind.WRITE).at_ms for c in (1, 2))
    assert last_read < first_write


async def test_lock_makes_the_second_call_wait(
    factory: SessionFactory, owner_id: int
) -> None:
    result = await run(factory, owner_id, Strategy.LOCK)

    assert result.final == 5
    assert not result.lost_update
    assert sorted(c.returned or 0 for c in result.calls) == [4, 5]
    assert all(c.attempts == 1 for c in result.calls)
    # Проигравший в гонке за блокировку читает только после коммита победителя.
    by_read = sorted((1, 2), key=lambda c: first(result, c, EventKind.READ).at_ms)
    winner, waiter = by_read
    assert (
        first(result, waiter, EventKind.READ).at_ms
        >= first(result, winner, EventKind.COMMIT).at_ms
    )
    # И ждал не меньше, чем победитель держал строку.
    waited = (
        first(result, waiter, EventKind.READ).at_ms
        - first(result, waiter, EventKind.LOCK_REQUESTED).at_ms
    )
    assert waited >= HOLD.total_seconds() * 1000 * 0.8
    assert result.elapsed_ms >= 2 * HOLD.total_seconds() * 1000


async def test_version_reports_a_conflict_and_retries(
    factory: SessionFactory, owner_id: int
) -> None:
    result = await run(factory, owner_id, Strategy.VERSION)

    assert result.final == 5
    assert result.final_version == 3
    assert not result.lost_update
    assert sorted(c.attempts for c in result.calls) == [1, 2]
    conflicts = [e for e in result.events if e.kind is EventKind.CONFLICT]
    assert len(conflicts) == 1
    # Проигравший повторяет с новой попыткой и успевает.
    loser = conflicts[0].call
    assert kinds(result, loser).count(EventKind.START) == 2
    assert kinds(result, loser)[-1] is EventKind.COMMIT


async def test_events_are_ordered_by_time(
    factory: SessionFactory, owner_id: int
) -> None:
    result = await run(factory, owner_id, Strategy.UNSAFE)

    times = [e.at_ms for e in result.events]
    assert times == sorted(times)
    per_call: defaultdict[int, list[EventKind]] = defaultdict(list)
    for e in result.events:
        per_call[e.call].append(e.kind)
    assert per_call[1] == [
        EventKind.START,
        EventKind.READ,
        EventKind.WRITE,
        EventKind.COMMIT,
    ]


@pytest.mark.parametrize("strategy", list(Strategy))
async def test_run_leaves_no_rows_behind(
    factory: SessionFactory, owner_id: int, admin_db: Db, strategy: Strategy
) -> None:
    await run(factory, owner_id, strategy)

    for table in ("monitors", "incidents"):
        row = admin_db.execute(f"SELECT count(*) FROM {table}").fetchone()  # pyright: ignore[reportCallIssue, reportArgumentType]
        assert row is not None
        assert row[0] == 0, table


async def test_run_cleans_up_even_when_a_call_fails(
    factory: SessionFactory,
    owner_id: int,
    admin_db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("сбой внутри попытки")

    monkeypatch.setattr(ConcurrencyDemoService, "_attempt", broken)

    with pytest.raises(RuntimeError):
        await run(factory, owner_id, Strategy.LOCK)

    row = admin_db.execute("SELECT count(*) FROM monitors").fetchone()
    assert row is not None
    assert row[0] == 0
