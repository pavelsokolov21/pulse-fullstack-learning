from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pulse.repositories.checks import DEFAULT_MAX_CHECKS_PER_MONITOR

# Хост и порт сюда не входят: их принимает запускающий процесс
# (`fastapi dev --host --port`, `uvicorn --host --port`), приложение их не читает.
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class ApiSettings(BaseSettings):
    """Настройки API из окружения (`PULSE_API_*`).

    Отдельный класс от CLI-шного `core.settings.Settings`: у них разные
    префиксы и разные потребители.
    """

    # hide_input_in_errors: иначе при слишком коротком секрете ValidationError
    # печатает его значение (input_value=...) в лог запуска.
    model_config = SettingsConfigDict(
        env_prefix="PULSE_API_", frozen=True, hide_input_in_errors=True
    )

    environment: Literal["dev", "prod"] = Field(
        default="dev", description="В prod документация /docs и /redoc отключается"
    )
    log_level: LogLevel = Field(default="INFO", description="Уровень логов pulse.*")
    max_checks_per_monitor: int = Field(
        default=DEFAULT_MAX_CHECKS_PER_MONITOR,
        ge=1,
        description="Сколько последних проверок хранить на монитор",
    )

    jwt_secret: SecretStr = Field(
        min_length=32,
        description="Ключ подписи JWT (HS256), не короче 32 символов",
    )
    access_token_ttl_minutes: int = Field(
        default=15, ge=1, description="Срок жизни access-токена, минуты"
    )
    refresh_token_ttl_days: int = Field(
        default=7, ge=1, description="Срок жизни refresh-токена, дни"
    )
    login_rate_limit_attempts: int = Field(
        default=5, ge=1, description="Попыток входа с одного адреса за окно"
    )
    login_rate_limit_window_seconds: int = Field(
        default=60, ge=1, description="Окно ограничения частоты входа, секунды"
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_case_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> ApiSettings:
    """Настройки процесса, прочитанные один раз.

    Кэш не видит изменений окружения: в тестах передавайте `ApiSettings(...)` в
    `create_app` или вызывайте `get_settings.cache_clear()`.
    """
    # Обязательные поля (jwt_secret) читаются из окружения, а не из аргументов.
    return ApiSettings()  # pyright: ignore[reportCallIssue]
