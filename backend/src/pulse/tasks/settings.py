from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pulse.repositories.database import ASYNC_URL_PREFIX
from pulse.repositories.redis_client import REDIS_URL_PREFIXES


class WorkerSettings(BaseSettings):
    """Настройки воркера из окружения (`PULSE_WORKER_*`).

    Отдельный класс от `ApiSettings`: воркеру не нужны ни ключ JWT, ни настройки
    кеша, а API не нужен адрес вебхука. Общий секрет-минимум у каждого процесса свой.
    """

    model_config = SettingsConfigDict(
        env_prefix="PULSE_WORKER_", frozen=True, hide_input_in_errors=True
    )

    broker_url: SecretStr = Field(
        description=(
            "Адрес Redis-брокера: redis://:пароль@хост:порт/номер_базы. Номер базы "
            "лучше отдельный от кеша приложения (там 0), например 1"
        ),
    )
    database_url: SecretStr = Field(
        description="Адрес PostgreSQL ролью pulse_app (воркер только читает проверки)",
    )
    notify_webhook_url: SecretStr | None = Field(
        default=None,
        description="Куда слать уведомления. Не задан: уведомления пишутся в лог",
    )

    @field_validator("broker_url")
    @classmethod
    def _redis_scheme(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(REDIS_URL_PREFIXES):
            raise ValueError("адрес должен начинаться с redis:// или rediss://")
        return value

    @field_validator("database_url")
    @classmethod
    def _async_postgres_url(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(ASYNC_URL_PREFIX):
            raise ValueError(f"адрес должен начинаться с {ASYNC_URL_PREFIX}")
        return value


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()  # pyright: ignore[reportCallIssue]
