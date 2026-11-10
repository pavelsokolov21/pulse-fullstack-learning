"""Миграции Alembic: применяются, откатываются, совпадают с ORM-моделями."""

import io
from contextlib import redirect_stdout
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

Db = psycopg.Connection[tuple[object, ...]]
Database = tuple[Config, Db]

TABLES = {
    "audit_log",
    "channels",
    "checks",
    "incidents",
    "monitor_channels",
    "monitors",
    "refresh_tokens",
    "users",
}


def tables(db: Db) -> set[str]:
    rows = db.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    ).fetchall()
    return {str(r[0]) for r in rows}


def version(db: Db) -> object:
    row = db.execute("SELECT version_num FROM alembic_version").fetchone()
    return None if row is None else row[0]


def test_history_has_a_single_head(migrations_db: Database) -> None:
    config, _ = migrations_db

    # Две «головы» значили бы, что две ветки миграций не слили.
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1


def test_upgrade_creates_every_table_and_records_the_version(
    migrations_db: Database,
) -> None:
    config, db = migrations_db

    command.upgrade(config, "head")

    assert tables(db) >= TABLES
    assert version(db) == ScriptDirectory.from_config(config).get_current_head()


def test_upgrade_keeps_constraint_names_from_the_schema(
    migrations_db: Database,
) -> None:
    config, db = migrations_db

    command.upgrade(config, "head")

    names = {
        str(r[0]) for r in db.execute("SELECT conname FROM pg_constraint").fetchall()
    }
    assert {
        "users_email_key",
        "monitors_owner_url_key",
        "monitors_timeout_below_interval",
        "checks_status_xor_error",
        "incidents_no_overlap",
    } <= names


def test_downgrade_to_base_removes_the_tables(migrations_db: Database) -> None:
    config, db = migrations_db
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    assert not TABLES & tables(db)
    assert tables(db) <= {"alembic_version"}


def test_upgrade_is_repeatable_after_downgrade(migrations_db: Database) -> None:
    config, db = migrations_db
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    assert tables(db) >= TABLES


def test_upgrade_twice_is_a_noop(migrations_db: Database) -> None:
    config, db = migrations_db
    command.upgrade(config, "head")

    command.upgrade(config, "head")

    assert tables(db) >= TABLES


def test_application_role_gets_its_grants_from_the_migration(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "head")

    row = db.execute(
        "SELECT has_table_privilege('pulse_app', 'checks', 'UPDATE'),"
        " has_table_privilege('pulse_app', 'checks', 'INSERT'),"
        " has_table_privilege('pulse_app', 'audit_log', 'DELETE')"
    ).fetchone()
    assert row == (False, True, False)


def test_orm_models_match_the_migrated_database(migrations_db: Database) -> None:
    config, _ = migrations_db
    command.upgrade(config, "head")

    # Падает с CommandError, если автогенерация нашла расхождение моделей и базы:
    # поменяли модель, но не написали миграцию (или наоборот).
    command.check(config)


def test_offline_mode_prints_sql_without_a_database(
    migrations_db: Database,
) -> None:
    config, _ = migrations_db
    output = io.StringIO()

    with redirect_stdout(output):
        command.upgrade(config, "head", sql=True)

    text = output.getvalue()
    assert "CREATE TABLE users" in text
    assert "INSERT INTO alembic_version" in text


def test_missing_url_is_reported_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PULSE_MIGRATE_DATABASE_URL", raising=False)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))

    with pytest.raises(RuntimeError, match="PULSE_MIGRATE_DATABASE_URL"):
        command.upgrade(config, "head")


def test_0002_adds_the_version_column_to_existing_incidents(
    migrations_db: Database,
) -> None:
    config, db = migrations_db
    command.upgrade(config, "0001")
    db.execute(
        "INSERT INTO users (email, password_hash, role)"
        " VALUES ('o@example.com', 'h', 'manager')"
    )
    db.execute(
        "INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)"
        " SELECT id, 'https://a.test', 300, 5 FROM users"
    )
    db.execute(
        "INSERT INTO incidents (monitor_id, started_at) SELECT id, now() FROM monitors"
    )

    command.upgrade(config, "0002")

    # Старые строки получают версию 1; новая строка тоже начинается с 1.
    row = db.execute("SELECT version FROM incidents").fetchone()
    assert row is not None
    assert row[0] == 1


def test_0002_downgrade_removes_the_version_column(migrations_db: Database) -> None:
    config, db = migrations_db
    command.upgrade(config, "head")

    command.downgrade(config, "0001")

    columns = {
        str(r[0])
        for r in db.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'incidents'"
        ).fetchall()
    }
    assert "version" not in columns
    assert "failed_checks" in columns
