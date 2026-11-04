"""ORM-модели: как строки таблиц выглядят для SQLAlchemy.

Схему в базе создают миграции Alembic (первая применяет `db/schema.sql`). Здесь
описаны только таблицы, которые читает приложение, и всё, что Alembic сравнивает
при автогенерации: столбцы, UNIQUE, индексы и внешние ключи (`alembic check` падает,
если модель разошлась с базой). CHECK не дублируются: их проверяет база, а
репозитории переводят нарушения в доменные ошибки по имени ограничения.

Модели не выходят за слой репозиториев: наружу отдаются неизменяемые dataclass из
`pulse.domain` (как DTO отдельно от entity в NestJS/TypeORM). Так сервисы и API не
зависят от SQLAlchemy и не получают «живые» объекты, привязанные к сессии.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012
        # Все времена в базе timestamptz: без этого SQLAlchemy создал бы «наивный»
        # timestamp, и сравнивать с aware-datetime приложения было бы нельзя.
        datetime: DateTime(timezone=True),
        str: Text,
        int: BigInteger,
    }


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="users_email_key"),)

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    email: Mapped[str]
    password_hash: Mapped[str]
    role: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RefreshTokenModel(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="refresh_tokens_token_hash_key"),
        Index("refresh_tokens_user_id_idx", "user_id"),
        Index("refresh_tokens_family_id_idx", "family_id"),
        Index("refresh_tokens_expires_at_idx", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    token_hash: Mapped[str]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MonitorModel(Base):
    __tablename__ = "monitors"
    __table_args__ = (
        UniqueConstraint("owner_id", "url", name="monitors_owner_url_key"),
        # Цель составного внешнего ключа из monitor_channels.
        UniqueConstraint("id", "owner_id", name="monitors_id_owner_key"),
    )

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    url: Mapped[str]
    # Целые секунды (см. комментарий в schema.sql); в домене это timedelta.
    interval_seconds: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[float] = mapped_column(Double, server_default=text("10"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CheckModel(Base):
    __tablename__ = "checks"
    # После INSERT сразу забирать значения, которые посчитала база (столбец `ok`):
    # RETURNING в том же запросе, без второго SELECT.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
    __table_args__ = (
        # Цель составного внешнего ключа из incidents.
        UniqueConstraint("id", "monitor_id", name="checks_id_monitor_key"),
        Index(
            "checks_monitor_id_checked_at_idx", "monitor_id", text("checked_at DESC")
        ),
    )

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    monitor_id: Mapped[int] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE")
    )
    url: Mapped[str]
    status_code: Mapped[int | None] = mapped_column(SmallInteger)
    latency_ms: Mapped[float] = mapped_column(Double)
    error: Mapped[str | None]
    checked_at: Mapped[datetime]
    # Правило «успех = ответ 200..399» считает база (GENERATED ... STORED), поэтому
    # оно не может разойтись со status_code. Для приложения столбец только читается.
    ok: Mapped[bool] = mapped_column(
        Boolean,
        Computed("COALESCE(status_code BETWEEN 200 AND 399, false)", persisted=True),
    )
