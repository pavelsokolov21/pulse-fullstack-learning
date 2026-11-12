"""Переименование checks.latency_ms -> duration_ms, шаг 1/3: expand

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-12

Переименование `ALTER TABLE ... RENAME COLUMN` мгновенное, но ломает работающий код: в
момент выката часть серверов ещё пишет `latency_ms`, а часть уже ждёт `duration_ms`.
Поэтому три миграции, и между ними код работает и со старой колонкой, и с новой:

  0005 expand    новая колонка + триггер, который держит обе колонки одинаковыми;
  0006 migrate   заполнить duration_ms для старых строк (батчами);
  (выкат)        код переключается на duration_ms;
  0007 contract  duration_ms NOT NULL, триггер и latency_ms удаляются.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Триггер срабатывает ДО проверки NOT NULL, поэтому и «старый» код (пишет только
# latency_ms), и «новый» (только duration_ms) вставляют строки без ошибок.
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
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Без NOT NULL и DEFAULT: добавление мгновенное, строки не переписываются.
    op.execute("ALTER TABLE checks ADD COLUMN duration_ms double precision")
    op.execute(SYNC_FUNCTION)
    op.execute(
        "CREATE TRIGGER checks_sync_duration BEFORE INSERT OR UPDATE ON checks"
        " FOR EACH ROW EXECUTE FUNCTION checks_sync_duration()"
    )
    # То же правило, что у latency_ms. NOT VALID: новые строки проверяются сразу, а
    # старые (duration_ms пока NULL) проверит VALIDATE в 0007, после заполнения.
    op.execute(
        "ALTER TABLE checks ADD CONSTRAINT checks_duration_finite_non_negative"
        " CHECK (duration_ms >= 0 AND duration_ms < 'Infinity'::double precision)"
        " NOT VALID"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TRIGGER checks_sync_duration ON checks")
    op.execute("DROP FUNCTION checks_sync_duration()")
    op.execute("ALTER TABLE checks DROP COLUMN duration_ms")
