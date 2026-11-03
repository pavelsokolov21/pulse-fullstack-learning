"""Заполняет базу Pulse тестовыми данными (Faker).

    uv run python db/seed.py                 # в пустую базу pulse_schema
    uv run python db/seed.py --reset         # сначала очистить таблицы

Данные грузятся от имени `pulse_app`, то есть той роли, под которой будет работать
приложение: заодно видно, что выданных ей прав на вставку хватает. Очистка
(`--reset`) требует прав владельца и идёт от `pulse_migrator`.

Загрузка выполняется одной транзакцией: если что-то не прошло ограничения схемы,
в базе не останется половины данных. Очистка отдельная (другая роль, другое
соединение): после неудачной загрузки с `--reset` таблицы останутся пустыми.

Умолчания рассчитаны на локальный Docker и содержат учебные пароли из README.
Свои подключения задаются флагами или переменными `PULSE_SEED_DSN` и
`PULSE_SEED_RESET_DSN`. Скрипт стирает данные, поэтому на нелокальный сервер
он без `--allow-remote` не подключается.
"""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from seed_data import ADMIN_EMAIL, SeedConfig, SeedData, generate

from pulse.core.passwords import PasswordHasher

DEMO_PASSWORD = "pulse-demo-password"
# Учебные пароли локальной разработки (см. backend/README.md), не для общих серверов.
DEFAULT_DSN = "postgresql://pulse_app:app@localhost:5432/pulse_schema"
DEFAULT_RESET_DSN = "postgresql://pulse_migrator:migrator@localhost:5432/pulse_schema"
LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})

TABLES = (
    "audit_log",
    "monitor_channels",
    "incidents",
    "checks",
    "channels",
    "monitors",
    "refresh_tokens",
    "users",
)
# Столько строк в одном INSERT: большие массивы параметров не нужны.
CHUNK = 5000

type Cursor = psycopg.Cursor[tuple[Any, ...]]


# Закрытый список типов, а не произвольная строка: имя типа подставляется в SQL.
type SqlType = Literal[
    "text",
    "bigint",
    "integer",
    "smallint",
    "double precision",
    "timestamptz",
    "uuid",
    "jsonb",
    "inet",
]


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: SqlType  # тип элемента массива: строки приводятся к нему в БД


def insert_many(
    cur: Cursor,
    table: str,
    columns: Sequence[Column],
    rows: Sequence[Sequence[object]],
    returning: Sequence[str] = (),
) -> list[tuple[Any, ...]]:
    """Вставляет строки пачками через unnest (по одному запросу на пачку).

    Массивы параметров (по одному на столбец) разворачиваются в строки на стороне
    БД: это быстрее, чем `executemany`, и позволяет вернуть значения столбцов
    `returning`, по которым вызывающий код находит выданные базой id.
    """
    returned: list[tuple[Any, ...]] = []
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        arrays = sql.SQL(", ").join(
            sql.SQL("%s::{}[]").format(sql.SQL(c.sql_type)) for c in columns
        )
        aliases = [sql.Identifier(f"c{i}") for i in range(len(columns))]
        query = sql.SQL(
            "INSERT INTO {table} ({columns}) SELECT {selected} "
            "FROM unnest({arrays}) WITH ORDINALITY AS u({aliases}, ord) ORDER BY ord"
        ).format(
            table=sql.Identifier(table),
            columns=sql.SQL(", ").join(sql.Identifier(c.name) for c in columns),
            selected=sql.SQL(", ").join(aliases),
            arrays=arrays,
            aliases=sql.SQL(", ").join(aliases),
        )
        if returning:
            query += sql.SQL(" RETURNING ") + sql.SQL(", ").join(
                sql.Identifier(name) for name in returning
            )
        params = [[row[i] for row in chunk] for i in range(len(columns))]
        cur.execute(query, params)
        if returning:
            returned.extend(cur.fetchall())
    return returned


def load(cur: Cursor, data: SeedData) -> None:
    """Загружает данные в порядке внешних ключей, подставляя выданные базой id."""
    user_ids: dict[str, int] = dict(
        insert_many(
            cur,
            "users",
            [
                Column("email", "text"),
                Column("password_hash", "text"),
                Column("role", "text"),
                Column("created_at", "timestamptz"),
            ],
            [(u.email, u.password_hash, u.role, u.created_at) for u in data.users],
            returning=("email", "id"),
        )
    )
    user_id = [user_ids[u.email] for u in data.users]

    insert_many(
        cur,
        "refresh_tokens",
        [
            Column("user_id", "bigint"),
            Column("family_id", "uuid"),
            Column("token_hash", "text"),
            Column("expires_at", "timestamptz"),
            Column("revoked_at", "timestamptz"),
            Column("created_at", "timestamptz"),
        ],
        [
            (
                user_id[t.user_ref],
                t.family_id,
                t.token_hash,
                t.expires_at,
                t.revoked_at,
                t.created_at,
            )
            for t in data.refresh_tokens
        ],
    )

    channel_ids = {
        (owner, name): id_
        for owner, name, id_ in insert_many(
            cur,
            "channels",
            [
                Column("owner_id", "bigint"),
                Column("name", "text"),
                Column("kind", "text"),
                Column("target", "text"),
                Column("created_at", "timestamptz"),
            ],
            [
                (user_id[c.owner_ref], c.name, c.kind, c.target, c.created_at)
                for c in data.channels
            ],
            returning=("owner_id", "name", "id"),
        )
    }
    channel_id = [channel_ids[(user_id[c.owner_ref], c.name)] for c in data.channels]

    monitor_ids = {
        (owner, url): id_
        for owner, url, id_ in insert_many(
            cur,
            "monitors",
            [
                Column("owner_id", "bigint"),
                Column("url", "text"),
                Column("interval_seconds", "integer"),
                Column("timeout_seconds", "double precision"),
                Column("created_at", "timestamptz"),
            ],
            [
                (
                    user_id[m.owner_ref],
                    m.url,
                    m.interval_seconds,
                    m.timeout_seconds,
                    m.created_at,
                )
                for m in data.monitors
            ],
            returning=("owner_id", "url", "id"),
        )
    }
    monitor_id = [monitor_ids[(user_id[m.owner_ref], m.url)] for m in data.monitors]

    insert_many(
        cur,
        "monitor_channels",
        [
            Column("monitor_id", "bigint"),
            Column("channel_id", "bigint"),
            Column("owner_id", "bigint"),
        ],
        [
            (
                monitor_id[link.monitor_ref],
                channel_id[link.channel_ref],
                user_id[link.owner_ref],
            )
            for link in data.monitor_channels
        ],
    )

    # Проверки находим по естественному ключу (монитор, время): порядок id,
    # который выдаёт БД, нам не нужен.
    check_ids = {
        (monitor, checked_at): id_
        for monitor, checked_at, id_ in insert_many(
            cur,
            "checks",
            [
                Column("monitor_id", "bigint"),
                Column("url", "text"),
                Column("status_code", "smallint"),
                Column("latency_ms", "double precision"),
                Column("error", "text"),
                Column("checked_at", "timestamptz"),
            ],
            [
                (
                    monitor_id[c.monitor_ref],
                    c.url,
                    c.status_code,
                    c.latency_ms,
                    c.error,
                    c.checked_at,
                )
                for c in data.checks
            ],
            returning=("monitor_id", "checked_at", "id"),
        )
    }
    check_id = [
        check_ids[(monitor_id[c.monitor_ref], c.checked_at)] for c in data.checks
    ]

    incident_ids = {
        (monitor, started): id_
        for monitor, started, id_ in insert_many(
            cur,
            "incidents",
            [
                Column("monitor_id", "bigint"),
                Column("started_at", "timestamptz"),
                Column("resolved_at", "timestamptz"),
                Column("failed_checks", "integer"),
                Column("last_error", "text"),
                Column("opened_by_check_id", "bigint"),
                Column("resolved_by_check_id", "bigint"),
            ],
            [
                (
                    monitor_id[i.monitor_ref],
                    i.started_at,
                    i.resolved_at,
                    i.failed_checks,
                    i.last_error,
                    check_id[i.opened_by_check_ref],
                    None
                    if i.resolved_by_check_ref is None
                    else check_id[i.resolved_by_check_ref],
                )
                for i in data.incidents
            ],
            returning=("monitor_id", "started_at", "id"),
        )
    }
    incident_id = [
        incident_ids[(monitor_id[i.monitor_ref], i.started_at)] for i in data.incidents
    ]

    entity_ids = {
        "user": user_id,
        "monitor": monitor_id,
        "channel": channel_id,
        "incident": incident_id,
    }
    insert_many(
        cur,
        "audit_log",
        [
            Column("occurred_at", "timestamptz"),
            Column("actor_id", "bigint"),
            Column("action", "text"),
            Column("entity_type", "text"),
            Column("entity_id", "bigint"),
            Column("details", "jsonb"),
            Column("request_id", "text"),
            Column("ip", "inet"),
        ],
        [
            (
                e.occurred_at,
                None if e.actor_ref is None else user_id[e.actor_ref],
                e.action,
                e.entity_type,
                None
                if e.entity_ref is None
                else entity_ids[e.entity_type][e.entity_ref],
                json.dumps(e.details),
                e.request_id,
                e.ip,
            )
            for e in data.audit_log
        ],
    )


def is_local(dsn: str) -> bool:
    """Подключение к этой машине: localhost или unix-сокет (путь начинается с /)."""
    host = conninfo_to_dict(dsn).get("host") or ""
    return host in LOCAL_HOSTS or str(host).startswith("/")


def database_is_empty(cur: Cursor) -> bool:
    cur.execute("SELECT NOT EXISTS (SELECT 1 FROM users)")
    row = cur.fetchone()
    return row is not None and bool(row[0])


def reset(dsn: str) -> None:
    """Очищает все таблицы и обнуляет счётчики id. Нужны права владельца."""
    with psycopg.connect(dsn) as conn:
        names = sql.SQL(", ").join(sql.Identifier(t) for t in TABLES)
        conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(names))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Тестовые данные Pulse (Faker)")
    parser.add_argument("--users", type=int, default=30, help="сколько пользователей")
    parser.add_argument(
        "--checks", type=int, default=200, help="проверок на монитор (до 14 дней)"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="одинаковый seed, одинаковые данные"
    )
    parser.add_argument(
        "--reset", action="store_true", help="очистить таблицы перед загрузкой"
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("PULSE_SEED_DSN", DEFAULT_DSN),
        help="подключение для загрузки (pulse_app), иначе PULSE_SEED_DSN",
    )
    parser.add_argument(
        "--reset-dsn",
        default=os.environ.get("PULSE_SEED_RESET_DSN", DEFAULT_RESET_DSN),
        help="подключение для очистки (pulse_migrator), иначе PULSE_SEED_RESET_DSN",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="разрешить нелокальный сервер (скрипт стирает и добавляет данные)",
    )
    args = parser.parse_args(argv)

    targets = [args.dsn, *([args.reset_dsn] if args.reset else [])]
    if not args.allow_remote and not all(is_local(dsn) for dsn in targets):
        print(
            "Сервер не локальный: скрипт добавляет и стирает данные. "
            "Если это точно нужно, добавьте --allow-remote.",
            file=sys.stderr,
        )
        return 2

    password_hash = asyncio.run(PasswordHasher().hash(DEMO_PASSWORD))
    data = generate(
        SeedConfig(users=args.users, checks_per_monitor=args.checks, seed=args.seed),
        password_hash,
    )

    if args.reset:
        reset(args.reset_dsn)
    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        if not database_is_empty(cur):
            print(
                "В базе уже есть данные. Добавьте --reset, чтобы очистить таблицы.",
                file=sys.stderr,
            )
            return 1
        load(cur, data)

    print("Загружено:")
    for table, count in data.counts().items():
        print(f"  {table:<17}{count:>7}")
    print(f"Вход: {ADMIN_EMAIL} или любой email из users, пароль {DEMO_PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
