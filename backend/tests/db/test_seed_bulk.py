"""Наполнение checks для замеров: объём, окно, доли, серии сбоев, воспроизводимость."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import psycopg
import pytest
import seed_bulk
from seed import Cursor
from seed_bulk import collect_conditions, fill, render_report

pytestmark = pytest.mark.usefixtures("clean_database")

Db = psycopg.Connection[tuple[object, ...]]
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def cursor(db: Db) -> Cursor:
    return cast("Cursor", db.cursor())


def add_monitors(db: Db, *intervals: int) -> list[int]:
    owner = db.execute(
        "INSERT INTO users (email, password_hash, role)"
        " VALUES ('owner@example.com', 'h', 'manager') RETURNING id"
    ).fetchone()
    assert owner is not None
    ids: list[int] = []
    for n, interval in enumerate(intervals):
        row = db.execute(
            "INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)"
            " VALUES (%s, %s, %s, 5) RETURNING id",
            (owner[0], f"https://m{n}.test", interval),
        ).fetchone()
        assert row is not None
        ids.append(cast("int", row[0]))
    return ids


def scalar(db: Db, query: str, *params: object) -> Any:
    row = db.execute(query, params).fetchone()  # pyright: ignore[reportArgumentType]
    assert row is not None
    return row[0]


def test_fills_up_to_the_target(admin_db: Db) -> None:
    monitors = add_monitors(admin_db, 60, 300, 3600)

    added = fill(cursor(admin_db), target=5000, now=NOW)

    total = scalar(admin_db, "SELECT count(*) FROM checks")
    assert total == added
    # Каждый монитор округляется вверх: перебор не больше числа мониторов.
    assert 5000 <= total < 5000 + len(monitors)


def test_second_run_adds_nothing(admin_db: Db) -> None:
    add_monitors(admin_db, 60, 300)
    fill(cursor(admin_db), target=2000, now=NOW)

    again = fill(cursor(admin_db), target=2000, now=NOW)

    assert again == 0
    assert scalar(admin_db, "SELECT count(*) FROM checks") >= 2000


def test_tops_up_existing_rows_instead_of_adding_the_whole_target(
    admin_db: Db,
) -> None:
    add_monitors(admin_db, 60)
    fill(cursor(admin_db), target=1000, now=NOW)

    fill(cursor(admin_db), target=3000, now=NOW)

    total = scalar(admin_db, "SELECT count(*) FROM checks")
    assert 3000 <= total < 3010


def test_history_stays_inside_the_window(admin_db: Db) -> None:
    add_monitors(admin_db, 60, 300)

    fill(cursor(admin_db), target=3000, days=7, now=NOW)

    oldest = scalar(admin_db, "SELECT min(checked_at) FROM checks")
    newest = scalar(admin_db, "SELECT max(checked_at) FROM checks")
    assert oldest >= NOW - timedelta(days=7)
    assert newest <= NOW
    # Окно заполнено целиком, а не прижато к одному краю.
    assert newest - oldest > timedelta(days=6, hours=20)


def test_window_is_a_parameter(admin_db: Db) -> None:
    add_monitors(admin_db, 60)

    fill(cursor(admin_db), target=1000, days=2, now=NOW)

    assert scalar(admin_db, "SELECT min(checked_at) FROM checks") >= NOW - timedelta(
        days=2
    )


def test_frequent_monitors_get_proportionally_more_checks(admin_db: Db) -> None:
    fast, slow = add_monitors(admin_db, 30, 300)

    fill(cursor(admin_db), target=11_000, now=NOW)

    fast_count = scalar(
        admin_db, "SELECT count(*) FROM checks WHERE monitor_id = %s", fast
    )
    slow_count = scalar(
        admin_db, "SELECT count(*) FROM checks WHERE monitor_id = %s", slow
    )
    assert 9.5 < fast_count / slow_count < 10.5


def test_every_monitor_gets_some_history(admin_db: Db) -> None:
    monitors = add_monitors(admin_db, 30, 86400)

    fill(cursor(admin_db), target=100, now=NOW)

    for monitor in monitors:
        assert (
            scalar(
                admin_db, "SELECT count(*) FROM checks WHERE monitor_id = %s", monitor
            )
            >= 1
        )


def test_checks_snapshot_the_monitor_url(admin_db: Db) -> None:
    add_monitors(admin_db, 60)

    fill(cursor(admin_db), target=500, now=NOW)

    assert (
        scalar(admin_db, "SELECT count(*) FROM checks WHERE url <> 'https://m0.test'")
        == 0
    )


def test_failures_are_a_small_share(admin_db: Db) -> None:
    add_monitors(admin_db, 60, 120, 300)

    fill(cursor(admin_db), target=20_000, now=NOW)

    failed = scalar(admin_db, "SELECT count(*) FROM checks WHERE NOT ok")
    assert 0.01 < failed / 20_000 < 0.10


def test_failures_come_in_runs_not_as_isolated_points(admin_db: Db) -> None:
    add_monitors(admin_db, 60)
    fill(cursor(admin_db), target=20_000, now=NOW)

    longest = scalar(
        admin_db,
        """
        SELECT max(run) FROM (
            SELECT count(*) AS run FROM (
                SELECT ok, checked_at,
                       row_number() OVER (ORDER BY checked_at)
                     - row_number() OVER (PARTITION BY ok ORDER BY checked_at) AS grp
                FROM checks
            ) s
            WHERE NOT ok
            GROUP BY grp
        ) runs
        """,
    )
    assert longest >= 10


def test_responses_have_no_error_and_failures_without_response_do(
    admin_db: Db,
) -> None:
    add_monitors(admin_db, 60)

    fill(cursor(admin_db), target=20_000, now=NOW)

    # Правило схемы (checks_status_xor_error) уже проверило каждую вставку; здесь
    # убеждаемся, что генератор выдаёт оба вида строк.
    assert scalar(admin_db, "SELECT count(*) FROM checks WHERE status_code IS NULL") > 0
    assert scalar(admin_db, "SELECT count(*) FROM checks WHERE status_code = 200") > 0
    assert scalar(admin_db, "SELECT count(*) FROM checks WHERE status_code = 503") > 0


def test_latency_is_plausible(admin_db: Db) -> None:
    add_monitors(admin_db, 60)

    fill(cursor(admin_db), target=5000, now=NOW)

    median = scalar(
        admin_db,
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)"
        " FROM checks WHERE status_code = 200",
    )
    assert 30 < median < 100


def test_same_seed_gives_the_same_data(admin_db: Db) -> None:
    add_monitors(admin_db, 60, 300)
    summary = (
        "SELECT count(*), sum(latency_ms), count(*) FILTER (WHERE ok),"
        " min(checked_at) FROM checks"
    )

    fill(cursor(admin_db), target=3000, seed=7, now=NOW)
    first = admin_db.execute(summary).fetchone()  # pyright: ignore[reportArgumentType]
    admin_db.execute("TRUNCATE checks RESTART IDENTITY CASCADE")
    fill(cursor(admin_db), target=3000, seed=7, now=NOW)

    assert admin_db.execute(summary).fetchone() == first  # pyright: ignore[reportArgumentType]


def test_different_seeds_give_different_data(admin_db: Db) -> None:
    add_monitors(admin_db, 60)
    summary = "SELECT sum(latency_ms) FROM checks"

    fill(cursor(admin_db), target=2000, seed=1, now=NOW)
    first = scalar(admin_db, summary)
    admin_db.execute("TRUNCATE checks RESTART IDENTITY CASCADE")
    fill(cursor(admin_db), target=2000, seed=2, now=NOW)

    assert scalar(admin_db, summary) != first


def test_without_monitors_it_refuses_with_a_hint(admin_db: Db) -> None:
    with pytest.raises(ValueError, match=r"seed\.py"):
        fill(cursor(admin_db), target=100, now=NOW)

    assert scalar(admin_db, "SELECT count(*) FROM checks") == 0


def test_application_role_has_enough_rights_to_load(admin_db: Db) -> None:
    add_monitors(admin_db, 60)
    admin_db.execute("SET ROLE pulse_app")
    try:
        added = fill(cursor(admin_db), target=500, now=NOW)
    finally:
        admin_db.execute("RESET ROLE")

    assert added >= 500


# --- условия замеров --------------------------------------------------------


def test_conditions_include_version_settings_and_row_counts(admin_db: Db) -> None:
    add_monitors(admin_db, 60)
    fill(cursor(admin_db), target=500, now=NOW)

    conditions = collect_conditions(cursor(admin_db))

    assert conditions["version"][0].isdigit()
    assert set(conditions["settings"]) == {
        "shared_buffers",
        "effective_cache_size",
        "work_mem",
        "random_page_cost",
    }
    assert conditions["rows"]["checks"] >= 500
    assert conditions["rows"]["monitors"] == 1
    assert set(conditions["rows"]) == set(seed_bulk.TABLES)
    assert set(conditions["sizes"]) == {"checks (таблица)", "checks (индексы)"}


def test_report_lists_everything_needed_to_compare_runs() -> None:
    report = render_report(
        {
            "version": "18.1",
            "settings": {"shared_buffers": "128MB", "random_page_cost": "4"},
            "sizes": {"checks (таблица)": "60 MB"},
            "rows": {"users": 30, "checks": 1_000_000},
        }
    )

    assert "PostgreSQL: 18.1" in report
    assert "shared_buffers: 128MB" in report
    assert "random_page_cost: 4" in report
    assert "размер checks (таблица): 60 MB" in report
    assert "| checks | 1 000 000 |" in report
    assert "| users | 30 |" in report


# --- запуск -----------------------------------------------------------------


def test_remote_server_is_refused_without_a_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = seed_bulk.main(["--dsn", "postgresql://u:p@db.example.com/x"])

    assert code == 2
    assert "--allow-remote" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--target", "--days"])
@pytest.mark.parametrize("value", ["0", "-5"])
def test_non_positive_numbers_are_rejected(flag: str, value: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        seed_bulk.main([flag, value])

    assert exit_info.value.code == 2
