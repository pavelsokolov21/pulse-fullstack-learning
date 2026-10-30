from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PULSE_", frozen=True)

    concurrency: int = Field(default=10, ge=1)
    retries: int = Field(default=2, ge=0, description="Число повторов после неудачи")
    retry_delay: float = Field(
        default=0.5, ge=0, description="Пауза между повторами, секунды"
    )
