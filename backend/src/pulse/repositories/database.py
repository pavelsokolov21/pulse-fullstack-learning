"""Подключение к PostgreSQL: движок, фабрика сессий, разбор ошибок драйвера.

Всё, что знает про SQLAlchemy и драйвер, живёт в слое репозиториев. Выше (сервисы,
API) видны только репозитории и алиасы типов отсюда.
"""

import psycopg
from sqlalchemy import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Сессия SQLAlchemy ≈ один «сеанс работы с базой»: Unit of Work + identity map +
# транзакция. Не клиент запросов вроде React Query: она не кеширует между запросами
# API и живёт ровно один HTTP-запрос (см. `api.deps.get_session`).
type Session = AsyncSession
type SessionFactory = async_sessionmaker[AsyncSession]

ASYNC_URL_PREFIX = "postgresql+psycopg://"


def create_engine(url: str) -> AsyncEngine:
    """Движок: пул соединений на процесс. Создаётся один раз при запуске."""
    parsed = make_url(url)
    # Время приходит из базы в часовом поясе сессии. Без явного UTC один и тот же
    # момент отдавался бы то как «...Z», то как «...+03:00», в зависимости от
    # настроек сервера. Опции соединения из адреса сохраняются.
    options = " ".join(["-c timezone=UTC", str(parsed.query.get("options", ""))])
    parsed = parsed.update_query_dict({"options": options.strip()})
    # pool_pre_ping: перед выдачей соединения из пула проверяет, что оно живо
    # (база могла перезапуститься), иначе первый запрос после этого падал бы.
    return create_async_engine(parsed, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    # expire_on_commit=False: после commit объекты не «протухают». Иначе первое же
    # обращение к атрибуту пошло бы за свежим значением в базу, а в async это
    # ошибка (неявный ввод-вывод вне await).
    return async_sessionmaker(engine, expire_on_commit=False)


def constraint_name(error: IntegrityError) -> str | None:
    """Имя нарушенного ограничения (`monitors_owner_url_key` и т.п).

    Имена заданы в db/schema.sql: по ним репозиторий отличает дубликат от других
    нарушений, а не по тексту сообщения.
    """
    original = error.orig
    if isinstance(original, psycopg.Error):
        return original.diag.constraint_name
    return None
