"""Миграции на живых данных: NOT NULL, индекс CONCURRENTLY, переименование в три шага.

Здесь проверяется не «таблицы создались», а свойства, ради которых миграции написаны
именно так: данные переживают шаг, обе версии кода работают между шагами, откат
возвращает данные, оборванная миграция откатывается так, как мы ожидаем.
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from psycopg import sql

import pulse.repositories.migration_support as support

Db = psycopg.Connection[tuple[object, ...]]
Database = tuple[Config, Db]

MIGRATIONS = Path(__file__).parents[2] / "migrations"


def version(db: Db) -> object:
    row = db.execute("SELECT version_num FROM alembic_version").fetchone()
    return None if row is None else row[0]


def scalar(db: Db, query: LiteralString) -> object:
    row = db.execute(query).fetchone()
    assert row is not None
    return row[0]


def seed_checks(db: Db, count: int, column: str = "latency_ms") -> int:
    """Пользователь, монитор и `count` проверок; значение колонки = номер строки."""
    db.execute(
        "INSERT INTO users (email, password_hash, role)"
        " VALUES ('m@example.com', 'h', 'manager') ON CONFLICT DO NOTHING"
    )
    db.execute(
        "INSERT INTO monitors (owner_id, url, interval_seconds)"
        " SELECT id, 'https://a.test', 60 FROM users WHERE email = 'm@example.com'"
        " ON CONFLICT DO NOTHING"
    )
    monitor_id = int(str(scalar(db, "SELECT id FROM monitors LIMIT 1")))
    db.execute(
        sql.SQL(
            "INSERT INTO checks (monitor_id, url, status_code, {column}, checked_at)"
            " SELECT %s, 'https://a.test', 200, n, now() - n * interval '1 second'"
            " FROM generate_series(1, %s) AS n"
        ).format(column=sql.Identifier(column)),
        (monitor_id, count),
    )
    return monitor_id


def insert_one(db: Db, monitor_id: int, **columns: float) -> None:
    names = sql.SQL(", ").join(sql.Identifier(name) for name in columns)
    db.execute(
        sql.SQL(
            "INSERT INTO checks (monitor_id, url, status_code, checked_at, {names})"
            " VALUES (%s, 'https://a.test', 200, now(), {values})"
        ).format(
            names=names, values=sql.SQL(", ").join(sql.Placeholder() * len(columns))
        ),
        (monitor_id, *columns.values()),
    )


def columns(db: Db) -> set[str]:
    rows = db.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid = 'checks'::regclass"
        " AND attnum > 0 AND NOT attisdropped"
    ).fetchall()
    return {str(r[0]) for r in rows}


def not_null(db: Db, column: str) -> bool:
    return bool(
        db.execute(
            "SELECT attnotnull FROM pg_attribute"
            " WHERE attrelid = 'checks'::regclass AND attname = %s",
            (column,),
        ).fetchone()
        == (True,)
    )


def constraint_names(db: Db) -> set[str]:
    rows = db.execute(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'checks'::regclass"
    ).fetchall()
    return {str(r[0]) for r in rows}


# --- 0003: NOT NULL колонка -------------------------------------------------


def test_region_is_filled_for_existing_rows_and_not_null(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0002")
    seed_checks(db, 5)

    command.upgrade(config, "0003")

    assert scalar(db, "SELECT count(*) FROM checks WHERE region = 'eu'") == 5
    assert not_null(db, "region")
    # Временный CHECK убран: остаётся только NOT NULL самой колонки.
    assert "checks_region_tmp_not_null" not in constraint_names(db)


def test_region_default_applies_to_new_rows(migrations_db: Database) -> None:
    config, db = migrations_db
    command.upgrade(config, "0003")
    monitor_id = seed_checks(db, 1)

    insert_one(db, monitor_id, latency_ms=1.0)

    assert scalar(db, "SELECT count(*) FROM checks WHERE region = 'eu'") == 2


def test_region_migration_can_be_repeated_after_a_half_done_run(
    migrations_db: Database,
) -> None:
    # Шаги после autocommit_block не откатываются вместе с миграцией, поэтому повтор
    # обязан переживать состояние «колонка и NOT VALID CHECK уже есть».
    config, db = migrations_db
    command.upgrade(config, "0002")
    seed_checks(db, 3)
    db.execute("ALTER TABLE checks ADD COLUMN region text DEFAULT 'eu'")
    db.execute(
        "ALTER TABLE checks ADD CONSTRAINT checks_region_tmp_not_null"
        " CHECK (region IS NOT NULL) NOT VALID"
    )

    command.upgrade(config, "0003")

    assert not_null(db, "region")
    assert version(db) == "0003"


def test_region_downgrade_removes_the_column_and_keeps_rows(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0003")
    seed_checks(db, 4)

    command.downgrade(config, "0002")

    assert "region" not in columns(db)
    assert scalar(db, "SELECT count(*) FROM checks") == 4


# --- 0004: индекс CONCURRENTLY ----------------------------------------------


def index_state(db: Db) -> tuple[bool, bool] | None:
    row = db.execute(
        "SELECT indisvalid, indisunique FROM pg_index"
        " WHERE indexrelid = to_regclass('checks_checked_at_idx')"
    ).fetchone()
    return None if row is None else (bool(row[0]), bool(row[1]))


def test_checked_at_index_is_created_valid(migrations_db: Database) -> None:
    config, db = migrations_db
    command.upgrade(config, "0003")
    seed_checks(db, 50)

    command.upgrade(config, "0004")

    assert index_state(db) == (True, False)


def test_invalid_leftover_of_a_failed_concurrent_build_is_replaced(
    migrations_db: Database,
) -> None:
    # Оборванный CREATE INDEX CONCURRENTLY оставляет индекс с indisvalid = false.
    # Здесь обрыв устроен дубликатами: UNIQUE по checked_at не строится.
    config, db = migrations_db
    command.upgrade(config, "0003")
    monitor_id = seed_checks(db, 1)
    db.execute(
        "INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)"
        " SELECT monitor_id, url, status_code, latency_ms, checked_at FROM checks"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY checks_checked_at_idx"
            " ON checks (checked_at)"
        )
    assert index_state(db) == (False, True)
    assert monitor_id

    command.upgrade(config, "0004")

    assert index_state(db) == (True, False)


def test_index_downgrade_drops_it(migrations_db: Database) -> None:
    config, db = migrations_db
    command.upgrade(config, "0004")

    command.downgrade(config, "0003")

    assert index_state(db) is None


def test_index_migration_is_in_the_sql_for_offline_mode(
    migrations_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = migrations_db

    command.upgrade(config, "0003:0004", sql=True)

    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS checks_checked_at_idx" in (
        capsys.readouterr().out
    )


# --- 0005-0007: latency_ms -> duration_ms -----------------------------------


def test_expand_keeps_both_columns_equal_for_old_and_new_code(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0005")
    monitor_id = seed_checks(db, 1)

    insert_one(db, monitor_id, latency_ms=11.0)  # старый код пишет старую колонку
    insert_one(db, monitor_id, duration_ms=22.0)  # новый код пишет новую

    rows = db.execute(
        "SELECT latency_ms, duration_ms FROM checks"
        " WHERE latency_ms IN (11, 22) ORDER BY latency_ms"
    ).fetchall()
    assert rows == [(11.0, 11.0), (22.0, 22.0)]


def test_expand_update_through_either_column_keeps_them_in_sync(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0005")
    seed_checks(db, 1)
    db.execute("UPDATE checks SET duration_ms = latency_ms")

    db.execute("UPDATE checks SET latency_ms = 5")
    assert scalar(db, "SELECT duration_ms FROM checks") == 5
    db.execute("UPDATE checks SET duration_ms = 9")
    assert scalar(db, "SELECT latency_ms FROM checks") == 9


def test_expand_enforces_the_same_rule_on_the_new_column(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0005")
    monitor_id = seed_checks(db, 1)

    with pytest.raises(psycopg.errors.CheckViolation):
        insert_one(db, monitor_id, duration_ms=-1.0)


def test_migrate_fills_old_rows_in_batches(
    migrations_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0004")
    seed_checks(db, 25)  # до триггера: duration_ms появится пустой
    command.upgrade(config, "0005")
    assert scalar(db, "SELECT count(*) FROM checks WHERE duration_ms IS NULL") == 25
    batches: list[int] = []
    real = support.copy_column_in_batches

    def record(number: int, changed: int) -> None:
        batches.append(changed)

    def spy(*args: object, **kwargs: object) -> int:
        kwargs["batch_size"] = 10
        kwargs["after_batch"] = record
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(support, "copy_column_in_batches", spy)

    command.upgrade(config, "0006")

    assert batches == [10, 10, 5]
    assert scalar(db, "SELECT count(*) FROM checks WHERE duration_ms IS NULL") == 0
    assert (
        scalar(db, "SELECT count(*) FROM checks WHERE duration_ms <> latency_ms") == 0
    )


def test_batch_copy_touches_only_rows_that_still_differ(
    migrations_db: Database,
) -> None:
    # Повторный проход ничего не пишет: иначе возобновление после обрыва заново
    # переписывало бы уже готовые строки (лишний WAL и мёртвые версии строк).
    config, db = migrations_db
    command.upgrade(config, "0004")
    seed_checks(db, 25)
    command.upgrade(config, "0005")
    url = str(config.attributes["url"])
    engine = sa.create_engine(url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            first = support.copy_column_in_batches(
                conn,
                table="checks",
                key="id",
                source="latency_ms",
                target="duration_ms",
                batch_size=10,
            )
            second = support.copy_column_in_batches(
                conn,
                table="checks",
                key="id",
                source="latency_ms",
                target="duration_ms",
                batch_size=10,
            )
    finally:
        engine.dispose()

    assert (first, second) == (25, 0)


def test_migrate_commits_every_batch_and_resumes_after_a_failure(
    migrations_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0004")
    seed_checks(db, 25)
    command.upgrade(config, "0005")
    real = support.copy_column_in_batches
    calls = {"batches": 0}

    def explode_on_third_batch(number: int, changed: int) -> None:
        calls["batches"] += 1
        if calls["batches"] == 3:
            raise RuntimeError("обрыв на третьей пачке")

    def failing(*args: object, **kwargs: object) -> int:
        kwargs["batch_size"] = 10
        kwargs["after_batch"] = explode_on_third_batch
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(support, "copy_column_in_batches", failing)

    with pytest.raises(RuntimeError, match="третьей"):
        command.upgrade(config, "0006")

    # Версия не сдвинулась, но три пачки уже зафиксированы: это и значит «коммит
    # после каждой пачки». Одна большая транзакция оставила бы 0 заполненных строк.
    assert version(db) == "0005"
    assert scalar(db, "SELECT count(*) FROM checks WHERE duration_ms IS NOT NULL") == 25

    monkeypatch.setattr(support, "copy_column_in_batches", real)
    command.upgrade(config, "0006")  # повтор доделывает остаток
    assert version(db) == "0006"
    assert scalar(db, "SELECT count(*) FROM checks WHERE duration_ms IS NULL") == 0


def test_contract_drops_the_old_column_and_the_trigger(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0004")
    seed_checks(db, 12)
    command.upgrade(config, "0006")

    command.upgrade(config, "0007")

    assert "latency_ms" not in columns(db)
    assert not_null(db, "duration_ms")
    assert (
        scalar(
            db, "SELECT count(*) FROM pg_trigger WHERE tgname = 'checks_sync_duration'"
        )
        == 0
    )
    assert scalar(db, "SELECT sum(duration_ms) FROM checks") == sum(range(1, 13))
    assert "checks_duration_finite_non_negative" in constraint_names(db)
    assert not {
        "checks_latency_finite_non_negative",
        "checks_duration_tmp_not_null",
    } & (constraint_names(db))


def test_contract_downgrade_restores_latency_ms_with_data(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0004")
    seed_checks(db, 12)
    command.upgrade(config, "0007")
    monitor_id = int(str(scalar(db, "SELECT id FROM monitors")))
    insert_one(db, monitor_id, duration_ms=99.0)  # строка, созданная новым кодом

    command.downgrade(config, "0006")

    assert not_null(db, "latency_ms")
    assert not not_null(db, "duration_ms")
    assert (
        scalar(
            db,
            "SELECT count(*) FROM checks WHERE latency_ms IS DISTINCT FROM duration_ms",
        )
        == 0
    )
    assert scalar(db, "SELECT latency_ms FROM checks WHERE duration_ms = 99") == 99
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_one(db, monitor_id, latency_ms=-5.0)
    # Старый код снова может писать только latency_ms: триггер вернулся.
    insert_one(db, monitor_id, latency_ms=7.0)
    assert scalar(db, "SELECT duration_ms FROM checks WHERE latency_ms = 7") == 7


def test_full_downgrade_and_upgrade_round_trip_keeps_rows(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0002")
    seed_checks(db, 20)

    command.upgrade(config, "head")
    command.downgrade(config, "0002")
    command.upgrade(config, "head")

    assert scalar(db, "SELECT count(*) FROM checks") == 20
    assert scalar(db, "SELECT sum(duration_ms) FROM checks") == sum(range(1, 21))


# --- DDL транзакционный: что откатывается при обрыве -------------------------

BROKEN = """
from alembic import op

revision = "{rev}"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade() -> None:
{body}


def downgrade() -> None:
    pass
"""


@pytest.fixture
def add_revision(
    migrations_db: Database, tmp_path: Path
) -> Callable[[str, str, str], None]:
    """Копирует каталог миграций во временный и позволяет дописать свои ревизии."""
    config, _ = migrations_db
    copy = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, copy, ignore=shutil.ignore_patterns("__pycache__"))
    # 0001 читает ../db/schema.sql относительно своего файла.
    (tmp_path / "db").mkdir()
    shutil.copy(MIGRATIONS.parent / "db" / "schema.sql", tmp_path / "db" / "schema.sql")
    config.set_main_option("script_location", str(copy))

    def add(rev: str, down: str, body: str) -> None:
        text = BROKEN.format(rev=rev, down=down, body=body)
        (copy / "versions" / f"{rev}_extra.py").write_text(text)

    return add


def test_failed_migration_rolls_back_all_its_ddl(
    migrations_db: Database, add_revision: Callable[[str, str, str], None]
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0007")
    add_revision(
        "0099",
        "0007",
        '    op.execute("ALTER TABLE monitors ADD COLUMN broken_a integer")\n'
        '    op.execute("CREATE INDEX broken_idx ON monitors (broken_a)")\n'
        '    op.execute("SELECT 1 / 0")',
    )

    with pytest.raises(Exception, match="division by zero"):
        command.upgrade(config, "0099")

    # DDL в PostgreSQL транзакционный: ни колонки, ни индекса, версия прежняя.
    assert version(db) == "0007"
    assert (
        scalar(db, "SELECT count(*) FROM pg_attribute WHERE attname = 'broken_a'") == 0
    )
    assert (
        scalar(db, "SELECT count(*) FROM pg_indexes WHERE indexname = 'broken_idx'")
        == 0
    )


def test_part_before_autocommit_block_survives_a_failure(
    migrations_db: Database, add_revision: Callable[[str, str, str], None]
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0007")
    add_revision(
        "0099",
        "0007",
        '    op.execute("ALTER TABLE monitors ADD COLUMN half_a integer")\n'
        "    with op.get_context().autocommit_block():\n"
        '        op.execute("ALTER TABLE monitors ADD COLUMN half_b integer")\n'
        '        op.execute("SELECT 1 / 0")',
    )

    with pytest.raises(Exception, match="division by zero"):
        command.upgrade(config, "0099")

    # autocommit_block сначала фиксирует всё сделанное до него, и каждая его команда
    # коммитится сама: версия прежняя, а половина изменений осталась. Поэтому такие
    # шаги пишут идемпотентными.
    assert version(db) == "0007"
    assert scalar(db, "SELECT count(*) FROM pg_attribute WHERE attname = 'half_a'") == 1
    assert scalar(db, "SELECT count(*) FROM pg_attribute WHERE attname = 'half_b'") == 1


def test_each_migration_runs_in_its_own_transaction(
    migrations_db: Database, add_revision: Callable[[str, str, str], None]
) -> None:
    # transaction_per_migration в env.py: упавшая вторая миграция не откатывает
    # первую, и блокировки первой не держатся, пока идёт вторая.
    config, db = migrations_db
    command.upgrade(config, "0007")
    add_revision(
        "0098", "0007", '    op.execute("ALTER TABLE monitors ADD COLUMN kept integer")'
    )
    add_revision("0099", "0098", '    op.execute("SELECT 1 / 0")')

    with pytest.raises(Exception, match="division by zero"):
        command.upgrade(config, "head")

    assert version(db) == "0098"
    assert scalar(db, "SELECT count(*) FROM pg_attribute WHERE attname = 'kept'") == 1
