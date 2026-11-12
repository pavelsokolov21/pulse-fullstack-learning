"""Окружение Alembic: как миграции подключаются к базе и что считают «схемой».

Миграции выполняются ролью-владельцем (`pulse_migrator`), а не ролью приложения
`pulse_app`: у приложения нет права менять схему. Адрес берётся из окружения
`PULSE_MIGRATE_DATABASE_URL` (формат `postgresql+psycopg://...`), в репозиторий он не
попадает: в нём пароль.
"""

import asyncio
import os
import warnings
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import SchemaItem

from pulse.repositories.models import Base

config = context.config

# Столбец checks.ok вычисляется базой (GENERATED): Alembic предупреждает, что менять
# его выражение автогенерацией нельзя. Меняется такой столбец вручную в миграции.
warnings.filterwarnings("ignore", message="Computed default on .* cannot be modified")
target_metadata = Base.metadata


def database_url() -> str:
    # attributes["url"]: так адрес передают тесты (минуя ini, где знак % значим).
    url = config.attributes.get("url") or os.environ.get("PULSE_MIGRATE_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Не задан адрес базы. Укажите PULSE_MIGRATE_DATABASE_URL: "
            "postgresql+psycopg://pulse_migrator:ПАРОЛЬ@127.0.0.1:5432/ИМЯ_БАЗЫ"
        )
    return str(url)


def include_object(
    obj: SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    # Модели описывают не все таблицы (incidents, channels, ... приложение пока не
    # использует). Без этого фильтра автогенерация предложила бы их удалить.
    return not (type_ == "table" and reflected and compare_to is None)


def run_migrations_offline() -> None:
    """Режим `--sql`: печатает SQL, не подключаясь к базе."""
    context.configure(
        url=database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        # Каждая миграция в своей транзакции. Иначе `upgrade head` с несколькими
        # ревизиями держит блокировки первой миграции, пока не закончится последняя.
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        # Каждая миграция в своей транзакции. Иначе `upgrade head` с несколькими
        # ревизиями держит блокировки первой миграции, пока не закончится последняя.
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: миграция это один короткий процесс, пул ей не нужен.
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
