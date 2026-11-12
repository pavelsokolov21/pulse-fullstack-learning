"""checks.region NOT NULL без долгой блокировки таблицы

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Не checks_region_not_null: под этим именем PG 18 хранит свой NOT NULL колонки.
CONSTRAINT = "checks_region_tmp_not_null"


def upgrade() -> None:
    # Первая часть идёт в транзакции миграции и обе команды мгновенные.
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Постоянный DEFAULT (PG 11+) не переписывает таблицу. Волатильный
    # (clock_timestamp(), gen_random_uuid()) переписал бы её целиком: на 1M строк
    # 1.4 с под ACCESS EXCLUSIVE против 0.3 мс.
    op.execute("ALTER TABLE checks ADD COLUMN IF NOT EXISTS region text DEFAULT 'eu'")
    # NOT VALID: существующие строки не проверяются, блокировка короткая.
    op.execute(f"ALTER TABLE checks DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE checks ADD CONSTRAINT {CONSTRAINT} "
        "CHECK (region IS NOT NULL) NOT VALID"
    )

    # Блокировки живут до конца транзакции. Если оставить VALIDATE здесь, ACCESS
    # EXCLUSIVE от ADD COLUMN продержался бы весь скан таблицы. autocommit_block
    # сначала фиксирует транзакцию (блокировка снята), потом каждая команда идёт
    # в своей. Цена: часть миграции уже не откатывается вместе с остальным, поэтому
    # шаги выше написаны идемпотентно (IF NOT EXISTS, DROP IF EXISTS), повтор безопасен.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        # Скан всей таблицы, но под SHARE UPDATE EXCLUSIVE: чтение и запись идут.
        op.execute(f"ALTER TABLE checks VALIDATE CONSTRAINT {CONSTRAINT}")
        # PG 12+: проверенный CHECK доказывает отсутствие NULL, скан пропускается.
        op.execute("ALTER TABLE checks ALTER COLUMN region SET NOT NULL")
        op.execute(f"ALTER TABLE checks DROP CONSTRAINT {CONSTRAINT}")
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Удаление колонки только помечает её удалённой в каталоге, строки не трогает.
    op.execute("ALTER TABLE checks DROP COLUMN IF EXISTS region")
