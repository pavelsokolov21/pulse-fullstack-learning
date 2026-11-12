"""Индекс checks(checked_at): запросы по времени без привязки к монитору

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "checks_checked_at_idx"


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY нельзя внутри транзакции, поэтому autocommit_block.
    # Обычный CREATE INDEX на 1M строк держит SHARE на таблицу ~0.2 с: все INSERT
    # стоят в очереди (замер с нагрузчиком: максимум вставки 187 мс против 16 мс).
    # CONCURRENTLY строится дольше (321 мс против 204), зато записи не мешает.
    with op.get_context().autocommit_block():
        if not context.is_offline_mode():
            _drop_invalid_leftover()
        op.create_index(
            INDEX,
            "checks",
            ["checked_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX,
            table_name="checks",
            postgresql_concurrently=True,
            if_exists=True,
        )


def _drop_invalid_leftover() -> None:
    # Оборванный CONCURRENTLY (отмена, deadlock, lock_timeout) оставляет индекс с
    # indisvalid = false. Он пишется при каждой вставке, но запросам не помогает, а
    # IF NOT EXISTS при повторе его бы молча «признал». Поэтому битый сносим.
    broken = op.get_bind().scalar(
        sa.text(
            "SELECT NOT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"
        ),
        {"name": INDEX},
    )
    if broken:
        op.drop_index(
            INDEX, table_name="checks", postgresql_concurrently=True, if_exists=True
        )
