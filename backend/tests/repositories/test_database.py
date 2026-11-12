import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.domain.monitors import Monitor
from pulse.repositories.database import (
    Session,
    constraint_name,
    create_session_factory,
)
from pulse.repositories.models import CheckModel, UserModel


async def test_tests_run_under_the_application_role(session: Session) -> None:
    # Права из схемы действуют и в тестах: иначе код с UPDATE checks прошёл бы
    # тесты суперпользователя и упал бы в бою.
    assert await session.scalar(text("SELECT current_user")) == "pulse_app"


async def test_application_role_cannot_update_checks(
    session: Session, monitor: Monitor
) -> None:
    await session.execute(
        text(
            "INSERT INTO checks (monitor_id, url, duration_ms, checked_at, status_code)"
            " VALUES (:m, 'https://a.test', 1, now(), 200)"
        ),
        {"m": monitor.id},
    )

    with pytest.raises(ProgrammingError, match="permission denied"):
        await session.execute(update(CheckModel).values(duration_ms=0))


async def test_application_role_cannot_touch_the_audit_log(session: Session) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        await session.execute(text("DELETE FROM audit_log"))


async def test_constraint_name_is_read_from_the_driver_error(session: Session) -> None:
    session.add(UserModel(email="Not-Lower@x.io", password_hash="h", role="manager"))

    with pytest.raises(IntegrityError) as exc_info:
        await session.flush()

    assert constraint_name(exc_info.value) == "users_email_normalized"


def test_constraint_name_is_none_for_a_foreign_error() -> None:
    assert constraint_name(IntegrityError("stmt", None, ValueError("x"))) is None


def test_sessions_do_not_expire_objects_on_commit(engine: AsyncEngine) -> None:
    # expire_on_commit=False: после commit атрибуты не требуют нового запроса, а
    # в async он невозможен (MissingGreenlet).
    assert create_session_factory(engine).kw["expire_on_commit"] is False
