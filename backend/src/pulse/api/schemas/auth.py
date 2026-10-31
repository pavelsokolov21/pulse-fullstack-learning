from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, SecretStr

from pulse.core.utils.normalize_email import normalize_email
from pulse.domain.users import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


def _normalize_if_str(value: object) -> object:
    return normalize_email(value) if isinstance(value, str) else value


# Нормализация идёт до проверки формата: " Alice@Example.COM " это тот же адрес.
Email = Annotated[EmailStr, BeforeValidator(_normalize_if_str)]


class RegisterRequest(BaseModel):
    """Тело POST /auth/register."""

    model_config = ConfigDict(extra="forbid")

    email: Email
    # SecretStr: пароль не попадёт в repr и логи, если модель где-то напечатают.
    password: Annotated[
        SecretStr, Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    ]


class UserRead(BaseModel):
    """Пользователь в ответе API. Хеша пароля здесь нет."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    email: str


class TokenResponse(BaseModel):
    """Ответ POST /auth/login по RFC 6749 (OAuth2 password flow)."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
