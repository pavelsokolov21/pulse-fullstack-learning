from datetime import UTC, datetime, timedelta

import pytest

from pulse.domain.checks import Check
from pulse.repositories.checks import DEFAULT_MAX_CHECKS_PER_MONITOR, CheckRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def add(repo: CheckRepository, monitor_id: int = 1, n: int = 0) -> Check:
    return repo.add(
        monitor_id=monitor_id,
        url="https://a.test",
        status_code=200,
        latency_ms=float(n),
        error=None,
        checked_at=NOW + timedelta(seconds=n),
        ok=True,
    )


def test_add_assigns_increasing_ids_across_monitors() -> None:
    repo = CheckRepository()

    ids = [add(repo, 1).id, add(repo, 2).id, add(repo, 1).id]

    assert ids == [1, 2, 3]


def test_add_keeps_all_given_fields() -> None:
    repo = CheckRepository()

    check = repo.add(
        monitor_id=4,
        url="https://x.test",
        status_code=None,
        latency_ms=12.5,
        error="Network Error: ConnectError",
        checked_at=NOW,
        ok=False,
    )

    assert check == Check(
        1, 4, "https://x.test", None, 12.5, "Network Error: ConnectError", NOW, False
    )


def test_list_recent_returns_newest_first() -> None:
    repo = CheckRepository()
    checks = [add(repo, n=n) for n in range(3)]

    assert repo.list_recent(1, 10) == list(reversed(checks))


def test_list_recent_applies_limit_to_newest() -> None:
    repo = CheckRepository()
    checks = [add(repo, n=n) for n in range(5)]

    assert repo.list_recent(1, 2) == [checks[4], checks[3]]


def test_list_recent_for_unknown_monitor_is_empty() -> None:
    assert CheckRepository().list_recent(99, 10) == []


def test_monitors_have_separate_histories() -> None:
    repo = CheckRepository()
    first = add(repo, monitor_id=1)
    second = add(repo, monitor_id=2)

    assert repo.list_recent(1, 10) == [first]
    assert repo.list_recent(2, 10) == [second]


def test_history_is_capped_and_oldest_are_dropped() -> None:
    repo = CheckRepository()
    checks = [add(repo, n=n) for n in range(DEFAULT_MAX_CHECKS_PER_MONITOR + 5)]

    stored = repo.list_recent(1, DEFAULT_MAX_CHECKS_PER_MONITOR + 50)

    assert len(stored) == DEFAULT_MAX_CHECKS_PER_MONITOR
    assert stored[0] == checks[-1]
    assert stored[-1] == checks[5]


def test_cap_is_per_monitor() -> None:
    repo = CheckRepository()
    for n in range(DEFAULT_MAX_CHECKS_PER_MONITOR):
        add(repo, monitor_id=1, n=n)
    other = add(repo, monitor_id=2)

    assert len(repo.list_recent(1, 1000)) == DEFAULT_MAX_CHECKS_PER_MONITOR
    assert repo.list_recent(2, 10) == [other]


def test_delete_for_monitor_removes_only_its_checks() -> None:
    repo = CheckRepository()
    add(repo, monitor_id=1)
    keep = add(repo, monitor_id=2)

    repo.delete_for_monitor(1)

    assert repo.list_recent(1, 10) == []
    assert repo.list_recent(2, 10) == [keep]


def test_delete_for_unknown_monitor_is_a_noop() -> None:
    CheckRepository().delete_for_monitor(42)


def test_ids_are_not_reused_after_delete() -> None:
    repo = CheckRepository()
    add(repo)
    repo.delete_for_monitor(1)

    assert add(repo).id == 2


# --- настраиваемый предел --------------------------------------------------


def test_custom_cap_limits_history() -> None:
    repo = CheckRepository(max_per_monitor=3)
    checks = [add(repo, n=n) for n in range(5)]

    assert repo.list_recent(1, 10) == [checks[4], checks[3], checks[2]]


def test_cap_of_one_keeps_only_the_latest() -> None:
    repo = CheckRepository(max_per_monitor=1)
    add(repo, n=0)
    latest = add(repo, n=1)

    assert repo.list_recent(1, 10) == [latest]


def test_default_cap_is_the_module_default() -> None:
    repo = CheckRepository()
    for n in range(DEFAULT_MAX_CHECKS_PER_MONITOR + 1):
        add(repo, n=n)

    assert len(repo.list_recent(1, 1000)) == DEFAULT_MAX_CHECKS_PER_MONITOR


@pytest.mark.parametrize("cap", [0, -1])
def test_non_positive_cap_is_rejected(cap: int) -> None:
    with pytest.raises(ValueError, match="max_per_monitor"):
        CheckRepository(max_per_monitor=cap)
