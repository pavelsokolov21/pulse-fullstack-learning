"""Базовая схема: 8 таблиц (users, refresh_tokens, monitors, checks, ...)

Revision ID: 0001
Revises:
Create Date: 2026-11-04
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op
from sqlalchemy import DDL

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Базовая схема живёт в db/schema.sql (с комментариями о каждом решении). Миграция
# применяет её как есть, поэтому файл после выпуска миграции не правят: всякое
# следующее изменение схемы это новая миграция.
SCHEMA = Path(__file__).parents[2] / "db" / "schema.sql"

TABLES = (
    "audit_log",
    "monitor_channels",
    "channels",
    "incidents",
    "checks",
    "monitors",
    "refresh_tokens",
    "users",
)


def upgrade() -> None:
    # BEGIN/COMMIT из файла убираем: транзакцией управляет Alembic. Если оставить,
    # COMMIT посреди миграции зафиксировал бы её раньше, чем запишется версия.
    lines = SCHEMA.read_text(encoding="utf-8").splitlines()
    body = "\n".join(
        line for line in lines if line.strip() not in ("BEGIN;", "COMMIT;")
    )
    # DDL, а не text(): текст уходит как есть, без разбора :имён как параметров.
    # Работает и в режиме `--sql`, где настоящего соединения нет.
    op.execute(DDL(body))


def downgrade() -> None:
    # Расширение btree_gist не удаляем: оно общее для базы, им могут пользоваться.
    op.execute(f"DROP TABLE {', '.join(TABLES)}")
