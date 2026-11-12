"""Переименование checks.latency_ms -> duration_ms, шаг 3/3: contract

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12

Применять только когда ВСЕ серверы работают на коде, читающем и пишущем duration_ms.
После этого шага latency_ms нет, и старый код упадёт.
"""

from collections.abc import Sequence

from alembic import op

from pulse.repositories.migration_support import copy_column_in_batches

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TMP = "checks_duration_tmp_not_null"
LATENCY_TMP = "checks_latency_tmp_not_null"

# Копия из 0005: миграция не должна зависеть от чужого файла, который могут поправить.
SYNC_FUNCTION = """
CREATE OR REPLACE FUNCTION checks_sync_duration() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.duration_ms IS NULL THEN
            NEW.duration_ms := NEW.latency_ms;
        ELSIF NEW.latency_ms IS NULL THEN
            NEW.latency_ms := NEW.duration_ms;
        END IF;
    ELSE
        IF NEW.latency_ms IS DISTINCT FROM OLD.latency_ms
           AND NEW.duration_ms IS NOT DISTINCT FROM OLD.duration_ms THEN
            NEW.duration_ms := NEW.latency_ms;
        ELSIF NEW.duration_ms IS DISTINCT FROM OLD.duration_ms
           AND NEW.latency_ms IS NOT DISTINCT FROM OLD.latency_ms THEN
            NEW.latency_ms := NEW.duration_ms;
        END IF;
    END IF;
    RETURN NEW;
END
$$
"""


def upgrade() -> None:
    # Часть 1 (без долгих блокировок): проверить все строки и сделать NOT NULL через
    # CHECK ... NOT VALID -> VALIDATE -> SET NOT NULL, как в 0003. Триггер ещё жив,
    # поэтому старый код в это время продолжает работать.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute(
            "ALTER TABLE checks VALIDATE CONSTRAINT checks_duration_finite_non_negative"
        )
        op.execute(f"ALTER TABLE checks DROP CONSTRAINT IF EXISTS {TMP}")
        op.execute(
            f"ALTER TABLE checks ADD CONSTRAINT {TMP}"
            " CHECK (duration_ms IS NOT NULL) NOT VALID"
        )
        op.execute(f"ALTER TABLE checks VALIDATE CONSTRAINT {TMP}")
        op.execute("ALTER TABLE checks ALTER COLUMN duration_ms SET NOT NULL")
        op.execute(f"ALTER TABLE checks DROP CONSTRAINT {TMP}")
        op.execute("RESET lock_timeout")

    # Часть 2: триггер и старая колонка уходят одной транзакцией. Окна, где триггера
    # уже нет, а latency_ms ещё есть, не бывает. DROP COLUMN только помечает колонку
    # удалённой в каталоге (строки не переписываются), вместе с ней уходит и CHECK.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TRIGGER checks_sync_duration ON checks")
    op.execute("DROP FUNCTION checks_sync_duration()")
    op.execute("ALTER TABLE checks DROP COLUMN latency_ms")


def downgrade() -> None:
    # Обратный путь тем же приёмом: колонка и триггер возвращаются сразу, данные
    # копируются батчами, NOT NULL и CHECK ставятся без долгой блокировки.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE checks ADD COLUMN latency_ms double precision")
    op.execute(SYNC_FUNCTION)
    op.execute(
        "CREATE TRIGGER checks_sync_duration BEFORE INSERT OR UPDATE ON checks"
        " FOR EACH ROW EXECUTE FUNCTION checks_sync_duration()"
    )
    op.execute(
        "ALTER TABLE checks ADD CONSTRAINT checks_latency_finite_non_negative"
        " CHECK (latency_ms >= 0 AND latency_ms < 'Infinity'::double precision)"
        " NOT VALID"
    )
    with op.get_context().autocommit_block():
        copy_column_in_batches(
            op.get_bind(),
            table="checks",
            key="id",
            source="duration_ms",
            target="latency_ms",
        )
        op.execute("SET lock_timeout = '5s'")
        op.execute(
            "ALTER TABLE checks VALIDATE CONSTRAINT checks_latency_finite_non_negative"
        )
        op.execute(f"ALTER TABLE checks DROP CONSTRAINT IF EXISTS {LATENCY_TMP}")
        op.execute(
            f"ALTER TABLE checks ADD CONSTRAINT {LATENCY_TMP}"
            " CHECK (latency_ms IS NOT NULL) NOT VALID"
        )
        op.execute(f"ALTER TABLE checks VALIDATE CONSTRAINT {LATENCY_TMP}")
        op.execute("ALTER TABLE checks ALTER COLUMN latency_ms SET NOT NULL")
        op.execute(f"ALTER TABLE checks DROP CONSTRAINT {LATENCY_TMP}")
        op.execute("ALTER TABLE checks ALTER COLUMN duration_ms DROP NOT NULL")
        op.execute("RESET lock_timeout")
