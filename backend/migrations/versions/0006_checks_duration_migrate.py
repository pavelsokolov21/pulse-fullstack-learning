"""Переименование checks.latency_ms -> duration_ms, шаг 2/3: migrate (данные)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-12

Заполняет duration_ms для строк, созданных до триггера. Пачки по 10 тысяч строк, коммит
после каждой: одна UPDATE на миллион строк держала бы блокировки строк всё время и
раздула бы WAL одной транзакцией.
"""

import time
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

from pulse.repositories.migration_support import BATCH_SIZE, copy_column_in_batches

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Пауза между пачками. В бою ставят 0.05-0.5 с, чтобы не забить диск и репликацию;
# здесь 0: база локальная, нагрузчик показывает, что и без паузы пауз хватает.
PAUSE_SECONDS = 0.0


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            sa.text("-- заполнение duration_ms батчами выполняется только онлайн")
        )
        return

    def pause(batches: int, changed: int) -> None:
        if PAUSE_SECONDS:
            time.sleep(PAUSE_SECONDS)

    # autocommit_block: сначала фиксирует транзакцию Alembic, затем каждый оператор
    # идёт в своей транзакции, то есть коммит после каждой пачки.
    with op.get_context().autocommit_block():
        copy_column_in_batches(
            op.get_bind(),
            table="checks",
            key="id",
            source="latency_ms",
            target="duration_ms",
            batch_size=BATCH_SIZE,
            after_batch=pause,
        )


def downgrade() -> None:
    # Откатывать нечего: заполненная duration_ms никому не мешает, а триггер и колонку
    # снимает downgrade шага 0005. Скопированные данные не «раскопировать» обратно.
    pass
