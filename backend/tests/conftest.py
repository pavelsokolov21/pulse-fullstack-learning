"""Общие фикстуры: тестовая база PostgreSQL.

Тесты репозиториев, сервисов и API ходят в настоящий Postgres: фейковая база не
знает ни ограничений схемы, ни реальных гонок. Нужен запущенный сервер
(`docker compose up -d`) и адрес тестовой базы:

* `PULSE_TEST_DATABASE_URL`, например
  `postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/pulse_test`, либо
* `POSTGRES_USER`/`POSTGRES_PASSWORD` из корневого `.env` (те же, что у compose).

Пользователь должен быть суперпользователем (так создан пользователь compose):
тесты создают базу `pulse_test`, применяют `db/schema.sql` и подключаются
приложением под ролью `pulse_app`, чтобы права из схемы действовали и в тестах
(например, `UPDATE checks` приложению запрещён). Имя базы обязано кончаться на
`_test`: файлы ниже её сбрасывают.
"""

import hashlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from urllib.parse import quote

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from pulse.domain.users import User
from pulse.repositories.database import Session, create_engine

BACKEND = Path(__file__).parents[1]
SCHEMA_FILE = BACKEND / "db" / "schema.sql"
APP_ROLE = "pulse_app"


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    pairs = (
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    return {key.strip(): value.strip() for key, value in pairs}


def _admin_url() -> URL | None:
    """Адрес тестовой базы от имени суперпользователя или `None`, если не задан."""
    explicit = os.environ.get("PULSE_TEST_DATABASE_URL")
    if explicit:
        return make_url(explicit)
    env = _read_dotenv(BACKEND.parent / ".env")
    user, password = env.get("POSTGRES_USER"), env.get("POSTGRES_PASSWORD")
    if user and password:
        return make_url(
            f"postgresql+psycopg://{quote(user)}:{quote(password)}"
            "@127.0.0.1:5432/pulse_test"
        )
    return None


ADMIN_URL = _admin_url()
# Приложение работает под ролью pulse_app: переключаем её на уровне соединения.
APP_URL = (
    ADMIN_URL.update_query_dict({"options": f"-c role={APP_ROLE}"})
    if ADMIN_URL
    else make_url("postgresql+psycopg://unconfigured:unconfigured@127.0.0.1/pulse_test")
)

# Приложение создаётся уже при импорте `pulse.api.main`, а его настройки требуют
# секрет JWT и адрес базы. Подключения при импорте нет: адрес только читается.
os.environ["PULSE_API_JWT_SECRET"] = "test-only-secret-not-for-production-use"
os.environ["PULSE_API_DATABASE_URL"] = APP_URL.render_as_string(hide_password=False)


def _libpq(url: URL, database: str | None = None) -> str:
    """Строка подключения для psycopg: он не понимает `+psycopg` в схеме."""
    url = url.set(drivername="postgresql")
    if database is not None:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


def ensure_database(admin: URL, name: str) -> URL:
    """Создаёт базу `name`, если её нет. Имя обязано кончаться на `_test`."""
    if not name.endswith("_test"):
        raise pytest.UsageError(
            f"Тестовая база должна называться *_test, а не {name!r}: "
            "тесты стирают в ней данные."
        )
    with psycopg.connect(_libpq(admin, "postgres"), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if exists is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return admin.set(database=name)


def alembic_config(url: URL) -> Config:
    """Конфигурация Alembic, направленная на базу `url` (минуя переменную окружения)."""
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["url"] = url.render_as_string(hide_password=False)
    return config


def reset_schema(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    connection.execute("DROP SCHEMA public CASCADE")
    connection.execute("CREATE SCHEMA public")
    # Права в схеме выдаются роли pulse_app: она должна существовать (общая на кластер).
    connection.execute(
        "DO $$ BEGIN IF NOT EXISTS "
        "(SELECT FROM pg_roles WHERE rolname = 'pulse_app') "
        "THEN CREATE ROLE pulse_app NOLOGIN; END IF; END $$"
    )


def _schema_marker() -> str:
    """Отпечаток всего, из чего строится схема: изменился файл, значит, пересоздать."""
    files = [SCHEMA_FILE, *sorted((BACKEND / "migrations" / "versions").glob("*.py"))]
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.read_bytes())
    return "pulse-schema:" + digest.hexdigest()


def _prepare_database(admin: URL) -> None:
    admin = ensure_database(admin, admin.database or "")
    marker = _schema_marker()
    with psycopg.connect(_libpq(admin), autocommit=True) as conn:
        row = conn.execute(
            "SELECT obj_description('public'::regnamespace, 'pg_namespace')"
        ).fetchone()
        if row is None or row[0] != marker:
            # Миграции изменились (или базы ещё нет): строим схему с нуля тем же
            # способом, что и в бою, то есть миграциями Alembic.
            reset_schema(conn)
            command.upgrade(alembic_config(admin), "head")
            conn.execute(
                sql.SQL("COMMENT ON SCHEMA public IS {}").format(sql.Literal(marker))
            )
        truncate_all(conn)


@pytest.fixture(scope="session")
def database_url() -> str:
    """Готовит тестовую базу один раз за прогон; возвращает адрес для приложения."""
    if ADMIN_URL is None:
        pytest.fail(
            "Не задана тестовая база. Укажите PULSE_TEST_DATABASE_URL или создайте "
            "корневой .env (cp .env.example .env) и запустите `docker compose up -d`."
        )
    try:
        _prepare_database(ADMIN_URL)
    except psycopg.OperationalError as exc:
        pytest.fail(
            f"PostgreSQL недоступен ({exc}). Запущен ли `docker compose up -d`?"
        )
    return APP_URL.render_as_string(hide_password=False)


@pytest.fixture
def admin_db(database_url: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """Прямое соединение суперпользователя: подготовка данных и очистка в тестах API."""
    assert ADMIN_URL is not None
    with psycopg.connect(_libpq(ADMIN_URL), autocommit=True) as connection:
        yield connection


@pytest.fixture
def migrations_db(
    database_url: str,
) -> Iterator[tuple[Config, psycopg.Connection[tuple[object, ...]]]]:
    """Пустая отдельная база для тестов миграций, её конфигурация и соединение.

    Своя база `pulse_migrations_test`: тесты сносят в ней схему, а общая `pulse_test`
    нужна остальным тестам нетронутой.
    """
    assert ADMIN_URL is not None
    url = ensure_database(ADMIN_URL, "pulse_migrations_test")
    with psycopg.connect(_libpq(url), autocommit=True) as connection:
        reset_schema(connection)
        yield alembic_config(url), connection
        reset_schema(connection)


@pytest.fixture
def clean_database(
    admin_db: psycopg.Connection[tuple[object, ...]],
) -> Iterator[None]:
    """Очищает таблицы до и после теста: для тестов, где коммиты настоящие.

    Приложение под `TestClient` живёт в своём потоке и своём event loop, поэтому
    разделить с ним одну откатываемую транзакцию нельзя: оно коммитит само.
    RESTART IDENTITY возвращает нумерацию с 1, и тесты могут ждать `"id": 1`.
    """
    truncate_all(admin_db)
    yield
    truncate_all(admin_db)


def truncate_all(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    connection.execute(
        "TRUNCATE users, refresh_tokens, monitors, checks, incidents, channels, "
        "monitor_channels, audit_log RESTART IDENTITY CASCADE"
    )


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[Session]:
    """Сессия, которую после теста откатывают: тесты не оставляют данных в базе.

    Сессия присоединяется к внешней транзакции соединения. `commit()` внутри кода
    приложения превращается в освобождение SAVEPOINT, а настоящий откат делаем в
    конце. Идентификаторы (IDENTITY) при откате не возвращаются: не опирайтесь на
    конкретные id.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as session:
            yield session
        await transaction.rollback()


type PersistUsers = Callable[..., Awaitable[None]]


@pytest.fixture
def persist_users(session: Session) -> PersistUsers:
    """Записывает готовые `User` в базу с их id (для тестов с константами-владельцами).

    Id задаётся явно (`OVERRIDING SYSTEM VALUE`: столбец GENERATED ALWAYS обычно не
    даёт), чтобы константы вроде `ALICE = User(1, ...)` совпадали со строками.
    Последовательность при этом не двигается: пользователей, созданных репозиторием
    в том же тесте, не смешивайте с этим способом.
    """

    async def persist(*users: User) -> None:
        for user in users:
            await session.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, role)"
                    " OVERRIDING SYSTEM VALUE VALUES (:id, :email, :hash, :role)"
                ),
                {
                    "id": user.id,
                    "email": user.email,
                    "hash": user.password_hash,
                    "role": user.role.value,
                },
            )

    return persist
