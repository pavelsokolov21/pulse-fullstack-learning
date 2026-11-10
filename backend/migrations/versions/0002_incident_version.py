"""Версия строки инцидента для оптимистичной блокировки

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD COLUMN берёт ACCESS EXCLUSIVE: если какая-то долгая транзакция держит
    # incidents, миграция встанет в очередь и заблокирует за собой всех. Лучше
    # быстро упасть и повторить, чем остановить запись в таблицу.
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Постоянное значение по умолчанию (PG 11+) не переписывает таблицу: старые
    # строки получают 1 без прохода по данным, миграция мгновенна и на больших
    # таблицах. CHECK не нужен: версию меняет только `version = version + 1`.
    op.execute("ALTER TABLE incidents ADD COLUMN version integer NOT NULL DEFAULT 1")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE incidents DROP COLUMN version")
