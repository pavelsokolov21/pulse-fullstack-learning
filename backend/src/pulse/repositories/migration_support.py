"""Помощники для миграций, которые двигают много строк.

Живёт в `repositories/`: это единственный слой, которому разрешено знать про SQLAlchemy.
"""

import logging
from collections.abc import Callable

import sqlalchemy as sa

logger = logging.getLogger("alembic.runtime.migration")

BATCH_SIZE = 10_000


def copy_column_in_batches(
    connection: sa.Connection,
    *,
    table: str,
    key: str,
    source: str,
    target: str,
    batch_size: int = BATCH_SIZE,
    after_batch: Callable[[int, int], None] | None = None,
) -> int:
    """Копирует `source` в `target` пачками по `batch_size` строк.

    Возвращает число скопированных строк.

    Вызывать только в режиме AUTOCOMMIT (`op.get_context().autocommit_block()`):
    тогда каждая пачка это отдельная короткая транзакция, а не одна на всю таблицу.
    Что это даёт, чего не даёт одна `UPDATE` на миллион строк:
      - блокировки строк держатся миллисекунды, а не всё время миграции;
      - при обрыве уже скопированное остаётся, повтор продолжит с места остановки
        (условие IS DISTINCT FROM пропускает готовые строки);
      - автовакуум успевает убирать мёртвые версии строк между пачками.

    Идём по первичному ключу (`key > after ORDER BY key LIMIT n`), а не по условию
    `target IS NULL`: иначе каждая следующая пачка сканировала бы таблицу сначала.
    Имена таблицы и колонок подставляются в запрос текстом: передавайте только
    константы из кода миграции, никогда не пользовательский ввод.
    """
    statement = sa.text(
        f"""
        WITH batch AS (
            SELECT {key} AS k FROM {table} WHERE {key} > :after ORDER BY {key} LIMIT :n
        ), changed AS (
            UPDATE {table} AS t SET {target} = t.{source}
            FROM batch
            WHERE t.{key} = batch.k AND t.{target} IS DISTINCT FROM t.{source}
            RETURNING 1
        )
        SELECT (SELECT max(k) FROM batch), (SELECT count(*) FROM changed)
        """
    )
    after = 0
    total = 0
    batches = 0
    while True:
        last, changed = connection.execute(
            statement, {"after": after, "n": batch_size}
        ).one()
        if last is None:
            return total
        after, total, batches = int(last), total + int(changed), batches + 1
        if after_batch is not None:
            after_batch(batches, int(changed))
        if batches % 20 == 0:
            logger.info("%s.%s -> %s: %d строк", table, source, target, total)
