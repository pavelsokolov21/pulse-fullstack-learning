"""Наполняет `checks` до нужного числа строк для замеров (фаза 2, неделя 4).

    uv run python db/seed_bulk.py                    # до 1 000 000 строк, окно 30 дней
    uv run python db/seed_bulk.py --target 2000000 --days 60

Сначала должны быть мониторы (`db/seed.py`): проверки раскладываются по ним. Скрипт
ДОПОЛНЯЕТ таблицу до `--target`: если там уже столько строк или больше, он ничего не
добавляет, поэтому повторный запуск не удваивает данные.

Генерирует сама база (`generate_series`), а не Python: миллион объектов в памяти и
пачки параметров были бы медленнее и тяжелее. Данные похожи на настоящие, чтобы планы
запросов были правдоподобны:

* окно истории `--days`: `checked_at` равномерно размазан по последним N дням;
* частый монитор даёт больше проверок: доля монитора пропорциональна 1 / interval;
* сбои идут сериями (простой сайта), а не случайными точками: 2% «окон» по 30 проверок
  подряд падают (в основном сетевая ошибка, остальное 503); вне простоев редкие 404/500;
* задержка у успешных ответов логнормальная (медиана около 55 мс), у сетевых ошибок
  порядка таймаута монитора;
* `--seed` делает результат воспроизводимым.

Вставка идёт от `pulse_app` (заодно видно, что её прав хватает), а `ANALYZE` и отчёт об
условиях замеров от `pulse_migrator`: `ANALYZE` требует прав владельца.

ВАЖНО для замеров: приложение после каждой проверки оставляет на монитор не больше
`PULSE_API_MAX_CHECKS_PER_MONITOR` строк (по умолчанию 100) и удалит всё остальное.
Пока меряете, запускайте API с большим значением (например 100000000) или не
запускайте проверки через него.
"""

import argparse
import os
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg import sql
from seed import DEFAULT_DSN, DEFAULT_RESET_DSN, TABLES, Cursor, is_local

DEFAULT_TARGET = 1_000_000
DEFAULT_DAYS = 30
# Простой это серия подряд идущих проверок монитора: столько их в одном «окне».
OUTAGE_WINDOW = 30
# Процент «окон», в которых сайт лежит целиком.
OUTAGE_PERCENT = 2

# Параметры подставляет psycopg (%(name)s). Оператор остатка записан функцией mod():
# знак % в тексте запроса с параметрами пришлось бы удваивать.
FILL_SQL = sql.SQL("""
WITH shares AS (
    SELECT id, url, timeout_seconds,
           (1.0 / interval_seconds) / sum(1.0 / interval_seconds) OVER () AS share
    FROM monitors
), plan AS (
    -- ceil и минимум 1: каждый монитор получает историю, а сумма не меньше цели.
    SELECT id, url, timeout_seconds,
           GREATEST(1, ceil(%(rows)s * share))::int AS n
    FROM shares
), points AS MATERIALIZED (
    SELECT p.id, p.url, p.timeout_seconds, p.n, g.i,
           -- Номер «окна» из 30 проверок монитора; хеш решает, лежит ли оно целиком.
           abs(mod(hashtextextended(p.id::text || ':' || (g.i / {window})::text,
                                    %(seed)s), 100)) AS window_roll,
           random() AS r_status, random() AS r_latency, random() AS r_jitter
    FROM plan p
    CROSS JOIN LATERAL generate_series(0, p.n - 1) AS g(i)
), classified AS (
    SELECT *,
           CASE
               WHEN window_roll < {outage} THEN
                   CASE WHEN r_status < 0.6 THEN NULL ELSE 503 END
               ELSE
                   CASE WHEN r_status < 0.965 THEN 200
                        WHEN r_status < 0.985 THEN 301
                        WHEN r_status < 0.995 THEN 404
                        ELSE 500 END
           END AS status
    FROM points
)
INSERT INTO checks (monitor_id, url, status_code, duration_ms, error, checked_at)
SELECT id, url, status,
       CASE WHEN status IS NULL
            THEN timeout_seconds * 1000 * (0.5 + 0.5 * r_latency)
            ELSE exp(4 + 1.2 * (r_latency + r_jitter - 1)) END,
       CASE WHEN status IS NULL THEN 'Network Error: ConnectError' END,
       -- Равномерно по окну; шум меньше шага, поэтому порядок сохраняется.
       %(start)s::timestamptz
           + (%(span)s * (i + r_jitter) / n) * interval '1 second'
FROM classified
""").format(window=sql.Literal(OUTAGE_WINDOW), outage=sql.Literal(OUTAGE_PERCENT))


def fill(
    cur: Cursor,
    *,
    target: int,
    days: int = DEFAULT_DAYS,
    seed: int = 42,
    now: datetime | None = None,
) -> int:
    """Добавляет проверки до `target` строк в таблице; возвращает, сколько добавлено."""
    cur.execute("SELECT count(*) FROM monitors")
    monitors = _scalar(cur)
    if monitors == 0:
        raise ValueError("Нет мониторов: сначала загрузите их (db/seed.py).")

    cur.execute("SELECT count(*) FROM checks")
    missing = target - _scalar(cur)
    if missing <= 0:
        return 0

    end = now or datetime.now(UTC)
    # setseed делает random() воспроизводимым в рамках этого соединения.
    cur.execute("SELECT setseed(%s)", [((seed % 2000) - 1000) / 1000])
    cur.execute(
        FILL_SQL,
        {
            "rows": missing,
            "seed": seed,
            "start": end - timedelta(days=days),
            "span": days * 86400,
        },
    )
    return cur.rowcount


def _scalar(cur: Cursor) -> int:
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


# --- условия замеров --------------------------------------------------------

SETTINGS = ("shared_buffers", "effective_cache_size", "work_mem", "random_page_cost")


def collect_conditions(cur: Cursor) -> dict[str, Any]:
    """Всё, без чего цифры замеров нельзя сравнивать: версия, настройки, объёмы."""
    cur.execute("SHOW server_version")
    version = str(_first(cur))
    settings: dict[str, str] = {}
    for name in SETTINGS:
        cur.execute("SELECT current_setting(%s)", [name])
        settings[name] = str(_first(cur))

    rows: dict[str, int] = {}
    sizes: dict[str, str] = {}
    for table in TABLES:
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        rows[table] = _scalar(cur)
    cur.execute(
        "SELECT c.relname, pg_size_pretty(pg_table_size(c.oid)), "
        "pg_size_pretty(pg_indexes_size(c.oid)) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = 'checks'"
    )
    size_row = cur.fetchone()
    if size_row is not None:
        sizes = {
            "checks (таблица)": str(size_row[1]),
            "checks (индексы)": str(size_row[2]),
        }
    return {"version": version, "settings": settings, "rows": rows, "sizes": sizes}


def _first(cur: Cursor) -> object:
    row = cur.fetchone()
    assert row is not None
    return row[0]


def render_report(conditions: Mapping[str, Any]) -> str:
    """Условия замеров в Markdown: для заметки рядом с таблицей результатов."""
    lines = [
        "### Условия замеров",
        "",
        f"- PostgreSQL: {conditions['version']}",
    ]
    lines += [f"- {name}: {value}" for name, value in conditions["settings"].items()]
    lines += [
        f"- размер {name}: {value}" for name, value in conditions["sizes"].items()
    ]
    lines += ["", "| Таблица | Строк |", "| --- | ---: |"]
    lines += [
        f"| {t} | {n:,} |".replace(",", " ") for t, n in conditions["rows"].items()
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Наполнить checks для замеров")
    parser.add_argument(
        "--target", type=_positive, default=DEFAULT_TARGET, help="строк в checks"
    )
    parser.add_argument(
        "--days", type=_positive, default=DEFAULT_DAYS, help="окно истории, дни"
    )
    parser.add_argument("--seed", type=int, default=42, help="воспроизводимость")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("PULSE_SEED_DSN", DEFAULT_DSN),
        help="подключение для вставки (pulse_app), иначе PULSE_SEED_DSN",
    )
    parser.add_argument(
        "--admin-dsn",
        default=os.environ.get("PULSE_SEED_RESET_DSN", DEFAULT_RESET_DSN),
        help="для ANALYZE и отчёта (pulse_migrator), иначе PULSE_SEED_RESET_DSN",
    )
    parser.add_argument(
        "--allow-remote", action="store_true", help="разрешить нелокальный сервер"
    )
    args = parser.parse_args(argv)

    if not args.allow_remote and not all(
        is_local(d) for d in (args.dsn, args.admin_dsn)
    ):
        print(
            "Сервер не локальный: скрипт добавляет миллионы строк. "
            "Если это точно нужно, добавьте --allow-remote.",
            file=sys.stderr,
        )
        return 2

    started = time.perf_counter()
    try:
        with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
            added = fill(cur, target=args.target, days=args.days, seed=args.seed)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - started
    print(f"Добавлено {added:,} проверок за {elapsed:.1f} с".replace(",", " "))

    # ANALYZE вне транзакции вставки: планировщику нужна свежая статистика.
    with psycopg.connect(args.admin_dsn, autocommit=True) as admin:
        admin.execute("ANALYZE")
        with admin.cursor() as cur:
            print()
            print(render_report(collect_conditions(cur)))
    return 0


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("нужно число не меньше 1")
    return number


if __name__ == "__main__":
    sys.exit(main())
