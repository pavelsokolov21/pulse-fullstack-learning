import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import seed
from psycopg import sql
from seed import CHUNK, Column, insert_many

COLUMNS = [
    Column("email", "text"),
    Column("score", "integer"),
    Column("seen", "timestamptz"),
]


class FakeCursor:
    """Запоминает запросы и отдаёт заготовленные строки вместо `RETURNING`."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.params: list[list[list[object]]] = []
        self.returned: list[list[tuple[Any, ...]]] = []

    def execute(self, query: sql.Composable, params: list[list[object]]) -> None:
        self.queries.append(query.as_string())
        self.params.append(params)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.returned.pop(0)


def run(rows: list[tuple[object, ...]], returning: tuple[str, ...] = ()) -> FakeCursor:
    cur = FakeCursor()
    cur.returned = [[("x", 1)] for _ in range(len(rows) // CHUNK + 1)]
    insert_many(cast("seed.Cursor", cur), "users", COLUMNS, rows, returning)
    return cur


SEEN = datetime(2026, 1, 1, tzinfo=UTC)


def test_rows_become_one_array_per_column() -> None:
    cur = run([("a@x.io", 1, SEEN), ("b@x.io", None, SEEN)])

    assert cur.params == [[["a@x.io", "b@x.io"], [1, None], [SEEN, SEEN]]]


def test_query_unnests_arrays_in_row_order() -> None:
    (query,) = run([("a@x.io", 1, SEEN)]).queries

    assert query == (
        'INSERT INTO "users" ("email", "score", "seen") SELECT "c0", "c1", "c2" '
        "FROM unnest(%s::text[], %s::integer[], %s::timestamptz[]) "
        'WITH ORDINALITY AS u("c0", "c1", "c2", ord) ORDER BY ord'
    )


def test_returning_columns_are_requested_and_collected() -> None:
    cur = FakeCursor()
    cur.returned = [[("a@x.io", 7)]]

    got = insert_many(
        cast("seed.Cursor", cur),
        "users",
        COLUMNS,
        [("a@x.io", 1, SEEN)],
        ("email", "id"),
    )

    assert got == [("a@x.io", 7)]
    assert cur.queries[0].endswith('ORDER BY ord RETURNING "email", "id"')


def test_without_returning_nothing_is_fetched() -> None:
    cur = run([("a@x.io", 1, SEEN)])

    assert "RETURNING" not in cur.queries[0]
    assert len(cur.returned) == 1  # fetchall не вызывался


def test_no_rows_means_no_queries() -> None:
    assert run([]).queries == []


def test_large_inserts_are_split_into_chunks_in_order() -> None:
    rows: list[tuple[object, ...]] = [
        (f"{n}@x.io", n, SEEN) for n in range(CHUNK * 2 + 1)
    ]
    cur = FakeCursor()
    cur.returned = [[(n,)] for n in range(3)]

    got = insert_many(cast("seed.Cursor", cur), "users", COLUMNS, rows, ("id",))

    assert [len(p[0]) for p in cur.params] == [CHUNK, CHUNK, 1]
    assert cur.params[0][0][0] == "0@x.io"
    assert cur.params[1][0][0] == f"{CHUNK}@x.io"
    assert got == [(0,), (1,), (2,)]


def test_table_and_column_names_are_quoted_as_identifiers() -> None:
    cur = FakeCursor()
    insert_many(cast("seed.Cursor", cur), 'we"ird', [Column('co"l', "text")], [("v",)])

    assert '"we""ird"' in cur.queries[0]
    assert '"co""l"' in cur.queries[0]


def test_values_never_appear_in_the_query_text() -> None:
    # Значения идут параметрами, а не подставляются в SQL.
    (query,) = run([("o'brien@x.io; DROP TABLE users", 1, SEEN)]).queries

    assert "DROP" not in query


def test_demo_password_is_long_enough_for_the_api() -> None:
    assert len(seed.DEMO_PASSWORD) >= 8


def test_default_dsns_use_the_least_privileged_role_for_loading() -> None:
    assert "pulse_app" in seed.DEFAULT_DSN
    assert "pulse_migrator" in seed.DEFAULT_RESET_DSN


def test_reset_covers_exactly_the_tables_of_the_schema() -> None:
    # Новая таблица в schema.sql без записи в TABLES осталась бы грязной после --reset.
    schema = (Path(__file__).parents[2] / "db" / "schema.sql").read_text(
        encoding="utf-8"
    )
    created = set(re.findall(r"^CREATE TABLE (\w+)", schema, flags=re.MULTILINE))

    assert set(seed.TABLES) == created
    assert len(seed.TABLES) == len(created)


# --- защита от случайного запуска на чужой базе -----------------------------


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@localhost:5432/db",
        "postgresql://u:p@127.0.0.1/db",
        "postgresql://u:p@[::1]/db",
        "host=localhost dbname=db user=u",
        "host=/var/run/postgresql dbname=db",
        "dbname=db user=u",  # без host: сокет по умолчанию
    ],
)
def test_local_connections_are_recognized(dsn: str) -> None:
    assert seed.is_local(dsn)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@db.prod.example.com/db",
        "postgresql://u:p@10.0.0.5:5432/db",
        "host=db.internal dbname=db",
        "host=localhost,db.prod.example.com dbname=db",  # локальный только первый
    ],
)
def test_remote_connections_are_recognized(dsn: str) -> None:
    assert not seed.is_local(dsn)


class Connected(Exception):
    """Тест перехватывает попытку подключения: до неё дойти не должны."""


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise Connected

    monkeypatch.setattr(seed.psycopg, "connect", refuse)


def test_remote_server_is_refused_before_any_work(
    no_database: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = seed.main(["--dsn", "postgresql://u:p@db.prod.example.com/db"])

    assert code == 2
    assert "--allow-remote" in capsys.readouterr().err


def test_remote_reset_server_is_refused_only_with_reset(
    no_database: None,
) -> None:
    remote = "postgresql://u:p@db.prod.example.com/db"

    assert seed.main(["--reset", "--reset-dsn", remote]) == 2
    # Без --reset этот адрес не используется, и проверка его не касается.
    with pytest.raises(Connected):
        seed.main(["--reset-dsn", remote])


def test_allow_remote_lifts_the_guard(no_database: None) -> None:
    with pytest.raises(Connected):
        seed.main(["--allow-remote", "--dsn", "postgresql://u:p@db.example.com/db"])


def test_dsn_defaults_come_from_the_environment(
    no_database: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PULSE_SEED_DSN", "postgresql://u:p@db.prod.example.com/db")

    assert seed.main([]) == 2
    assert "--allow-remote" in capsys.readouterr().err


def test_flag_wins_over_the_environment(
    no_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PULSE_SEED_DSN", "postgresql://u:p@db.prod.example.com/db")

    with pytest.raises(Connected):
        seed.main(["--dsn", "postgresql://u:p@localhost/db"])
