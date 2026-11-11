import json
import logging
from datetime import UTC, datetime

import pytest

from pulse.domain.reports import MonitorReport, ReportWindow
from pulse.repositories.redis_client import RedisClient, create_redis
from pulse.repositories.report_cache import CacheLookup, ReportCache

WEEK = ReportWindow.WEEK
DAY = ReportWindow.DAY
KEY = "pulse:report:1:7d"
VERSION_KEY = "pulse:report:ver:1"


def make_report(
    monitor_id: int = 1, window: ReportWindow = WEEK, total: int = 10
) -> MonitorReport:
    return MonitorReport(
        monitor_id=monitor_id,
        window=window,
        total=total,
        ok=9,
        uptime_percent=90.0,
        avg_latency_ms=55.5,
        p95_latency_ms=120.25,
        generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def cache(redis_client: RedisClient) -> ReportCache:
    return ReportCache(redis_client, ttl_seconds=60)


async def test_miss_reports_the_current_version(cache: ReportCache) -> None:
    assert await cache.get(1, WEEK) == CacheLookup(None, 0)


async def test_stored_report_comes_back_equal(cache: ReportCache) -> None:
    report = make_report()
    await cache.put(1, WEEK, report, version=0)

    lookup = await cache.get(1, WEEK)

    assert lookup.report == report
    assert lookup.version == 0


async def test_report_with_empty_values_survives_the_round_trip(
    cache: ReportCache,
) -> None:
    empty = MonitorReport(
        1, WEEK, 0, 0, None, None, None, datetime(2026, 1, 1, tzinfo=UTC)
    )
    await cache.put(1, WEEK, empty, version=0)

    assert (await cache.get(1, WEEK)).report == empty


async def test_invalidate_makes_the_stored_report_unreachable(
    cache: ReportCache,
) -> None:
    await cache.put(1, WEEK, make_report(), version=0)

    await cache.invalidate(1)

    assert await cache.get(1, WEEK) == CacheLookup(None, 1)


async def test_report_computed_before_an_invalidation_is_never_served(
    cache: ReportCache,
) -> None:
    # Гонка: читатель взял версию и ушёл считать; за это время пришла запись и сбросила
    # кеш; читатель кладёт свой (уже устаревший) результат. Он не должен вернуться.
    lookup = await cache.get(1, WEEK)
    await cache.invalidate(1)

    assert lookup.version is not None
    await cache.put(1, WEEK, make_report(total=999), lookup.version)

    assert (await cache.get(1, WEEK)).report is None


async def test_report_computed_after_an_invalidation_is_served(
    cache: ReportCache,
) -> None:
    await cache.invalidate(1)
    lookup = await cache.get(1, WEEK)
    assert lookup.version is not None

    await cache.put(1, WEEK, make_report(), lookup.version)

    assert (await cache.get(1, WEEK)).report == make_report()


async def test_invalidate_affects_every_window_of_the_monitor(
    cache: ReportCache,
) -> None:
    await cache.put(1, WEEK, make_report(window=WEEK), version=0)
    await cache.put(1, DAY, make_report(window=DAY), version=0)

    await cache.invalidate(1)

    assert (await cache.get(1, WEEK)).report is None
    assert (await cache.get(1, DAY)).report is None


async def test_invalidate_does_not_touch_other_monitors(cache: ReportCache) -> None:
    await cache.put(2, WEEK, make_report(monitor_id=2), version=0)

    await cache.invalidate(1)

    assert (await cache.get(2, WEEK)).report == make_report(monitor_id=2)


async def test_windows_are_cached_separately(cache: ReportCache) -> None:
    await cache.put(1, WEEK, make_report(window=WEEK, total=7), version=0)
    await cache.put(1, DAY, make_report(window=DAY, total=1), version=0)

    week = (await cache.get(1, WEEK)).report
    day = (await cache.get(1, DAY)).report

    assert week is not None
    assert day is not None
    assert (week.total, day.total) == (7, 1)


async def test_entry_has_a_ttl(cache: ReportCache, redis_client: RedisClient) -> None:
    await cache.put(1, WEEK, make_report(), version=0)

    assert 0 < await redis_client.ttl(KEY) <= 60


async def test_version_counter_outlives_cached_entries(
    cache: ReportCache, redis_client: RedisClient
) -> None:
    # Иначе после истечения счётчика версия вернулась бы к 0 и совпала бы с версией
    # давно устаревшей записи.
    await cache.invalidate(1)

    assert await redis_client.ttl(VERSION_KEY) > 24 * 3600


async def test_entry_expires_after_ttl(redis_client: RedisClient) -> None:
    import asyncio

    short = ReportCache(redis_client, ttl_seconds=1)
    await short.put(1, WEEK, make_report(), version=0)

    await asyncio.sleep(1.1)

    assert (await short.get(1, WEEK)).report is None


@pytest.mark.parametrize(
    "garbage",
    [
        "not json at all",
        "[]",
        "{}",
        json.dumps({"version": 0}),
        json.dumps({"version": 0, "report": {"monitor_id": 1}}),
        json.dumps({"version": 0, "report": {**{"monitor_id": 1}, "window": "1y"}}),
    ],
)
async def test_corrupted_entry_is_a_miss_not_an_error(
    cache: ReportCache,
    redis_client: RedisClient,
    garbage: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await redis_client.set(KEY, garbage)

    with caplog.at_level(logging.WARNING, logger="pulse.cache"):
        lookup = await cache.get(1, WEEK)

    assert lookup == CacheLookup(None, 0)


async def test_redis_outage_degrades_to_no_cache(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dead = create_redis("redis://:x@127.0.0.1:1/0")
    cache = ReportCache(dead, ttl_seconds=60)

    with caplog.at_level(logging.WARNING, logger="pulse.cache"):
        lookup = await cache.get(1, WEEK)
        await cache.put(1, WEEK, make_report(), version=0)  # не бросает
        await cache.invalidate(1)  # не бросает
    await dead.aclose()

    # Версия неизвестна: сохранять расчёт нельзя, и сервис пропустит `put`.
    assert lookup == CacheLookup(None, None)
    assert "Redis недоступен" in caplog.text


def test_ttl_must_be_positive(redis_client: RedisClient) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        ReportCache(redis_client, ttl_seconds=0)
