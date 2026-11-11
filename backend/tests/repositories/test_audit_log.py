"""Журнал аудита: запись, чтение, ограничения схемы, права приложения."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from pulse.core.request_context import request_id_var
from pulse.domain.monitors import Monitor
from pulse.domain.users import Role, User
from pulse.repositories.audit_log import AuditLogRepository
from pulse.repositories.database import Session
from pulse.repositories.users import UserRepository


@pytest.fixture
def audit(session: Session) -> AuditLogRepository:
    return AuditLogRepository(session)


async def test_record_stores_every_field(
    audit: AuditLogRepository, owner: User, monitor: Monitor
) -> None:
    await audit.record(
        actor_id=owner.id,
        action="monitor.update",
        entity_type="monitor",
        entity_id=monitor.id,
        details={"changes": {"timeout": {"from": 5.0, "to": 7.5}}, "n": 1},
    )

    [entry] = await audit.list_for_entity("monitor", monitor.id)

    assert entry.actor_id == owner.id
    assert entry.action == "monitor.update"
    assert (entry.entity_type, entry.entity_id) == ("monitor", monitor.id)
    assert entry.details == {"changes": {"timeout": {"from": 5.0, "to": 7.5}}, "n": 1}
    assert entry.occurred_at.tzinfo is not None
    assert entry.request_id is None  # вне запроса


async def test_record_takes_the_request_id_from_the_context(
    audit: AuditLogRepository, owner: User, monitor: Monitor
) -> None:
    token = request_id_var.set("req-123")
    try:
        await audit.record(
            actor_id=owner.id,
            action="monitor.create",
            entity_type="monitor",
            entity_id=monitor.id,
        )
    finally:
        request_id_var.reset(token)

    [entry] = await audit.list_for_entity("monitor", monitor.id)
    assert entry.request_id == "req-123"
    assert entry.details == {}


async def test_history_is_ordered_oldest_first_and_per_entity(
    audit: AuditLogRepository, owner: User, monitor: Monitor, other_monitor: Monitor
) -> None:
    for action in ("monitor.create", "monitor.update", "monitor.delete"):
        await audit.record(
            actor_id=owner.id,
            action=action,
            entity_type="monitor",
            entity_id=monitor.id,
        )
    await audit.record(
        actor_id=owner.id,
        action="monitor.create",
        entity_type="monitor",
        entity_id=other_monitor.id,
    )

    actions = [e.action for e in await audit.list_for_entity("monitor", monitor.id)]

    assert actions == ["monitor.create", "monitor.update", "monitor.delete"]


@pytest.mark.parametrize(
    "action", ["create", "Monitor.create", "monitor.", "monitor.create.again", ""]
)
async def test_database_rejects_a_malformed_action(
    audit: AuditLogRepository, owner: User, action: str
) -> None:
    with pytest.raises(IntegrityError, match="audit_log_action_format"):
        await audit.record(
            actor_id=owner.id, action=action, entity_type="monitor", entity_id=1
        )


async def test_database_rejects_an_unknown_entity_type(
    audit: AuditLogRepository, owner: User
) -> None:
    with pytest.raises(IntegrityError, match="audit_log_entity_type_valid"):
        await audit.record(
            actor_id=owner.id, action="thing.create", entity_type="thing", entity_id=1
        )


async def test_entry_outlives_the_deleted_user(
    session: Session, audit: AuditLogRepository
) -> None:
    gone = await UserRepository(session).create(
        email="gone@example.com", password_hash="h", role=Role.MANAGER
    )
    await audit.record(
        actor_id=gone.id, action="monitor.create", entity_type="monitor", entity_id=777
    )

    await session.execute(text("DELETE FROM users WHERE id = :i"), {"i": gone.id})

    [entry] = await audit.list_for_entity("monitor", 777)
    assert entry.actor_id is None  # SET NULL: журнал остался, автора нет


async def test_entry_outlives_the_deleted_monitor(
    session: Session, audit: AuditLogRepository, owner: User, monitor: Monitor
) -> None:
    await audit.record(
        actor_id=owner.id,
        action="monitor.create",
        entity_type="monitor",
        entity_id=monitor.id,
    )

    await session.execute(text("DELETE FROM monitors WHERE id = :i"), {"i": monitor.id})

    assert len(await audit.list_for_entity("monitor", monitor.id)) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'monitor.hacked'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
async def test_application_role_cannot_rewrite_or_erase_the_log(
    session: Session, audit: AuditLogRepository, owner: User, statement: str
) -> None:
    await audit.record(
        actor_id=owner.id, action="monitor.create", entity_type="monitor", entity_id=1
    )

    with pytest.raises(DBAPIError, match="permission denied"):
        await session.execute(text(statement))
