from dataclasses import dataclass

from pulse.core.passwords import PasswordHasher
from pulse.core.utils.normalize_email import normalize_email
from pulse.domain.errors import (
    DuplicateUserError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    InvalidTokenError,
)
from pulse.domain.users import MAX_PASSWORD_LENGTH, Role, User
from pulse.repositories.users import UserRepository
from pulse.services.refresh_tokens import IssuedRefreshToken, RefreshTokenService
from pulse.services.tokens import AccessToken, TokenService


@dataclass(frozen=True)
class TokenPair:
    access: AccessToken
    refresh: IssuedRefreshToken


class AuthService:
    """Регистрация, вход по паролю, обновление сессии и проверка токена."""

    def __init__(
        self,
        users: UserRepository,
        hasher: PasswordHasher,
        tokens: TokenService,
        refresh_tokens: RefreshTokenService,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._tokens = tokens
        self._refresh_tokens = refresh_tokens

    async def register(
        self, email: str, password: str, role: Role = Role.MANAGER
    ) -> User:
        """Роль не приходит из запроса API: её выдаёт код (или админ позже)."""
        email = normalize_email(email)
        password_hash = await self._hasher.hash(password)

        # Проверка стоит после `await`, прямо перед созданием, без await между
        # ними: пока хешировался пароль, такой же email мог зарегистрироваться
        # параллельным запросом. В базе это закроет UNIQUE-ограничение.
        if self._users.find_by_email(email) is not None:
            raise DuplicateUserError
        return self._users.create(email=email, password_hash=password_hash, role=role)

    async def login(self, email: str, password: str) -> TokenPair:
        # Слишком длинный пароль не хешируем: это дорогая операция на ровном месте.
        if len(password) > MAX_PASSWORD_LENGTH:
            raise InvalidCredentialsError

        user = self._users.find_by_email(normalize_email(email))
        if user is None:
            # Нет такого email, но время ответа должно совпадать с неверным паролем.
            await self._hasher.verify_dummy(password)
            raise InvalidCredentialsError

        if not await self._hasher.verify(password, user.password_hash):
            raise InvalidCredentialsError
        return TokenPair(
            self._tokens.issue(user.id), self._refresh_tokens.issue(user.id)
        )

    def refresh(self, refresh_token: str) -> TokenPair:
        """Обновляет сессию: старый refresh отзывается, выдаётся новая пара."""
        user_id, new_refresh = self._refresh_tokens.rotate(refresh_token)
        if self._users.get(user_id) is None:
            self._refresh_tokens.revoke(new_refresh.token)
            raise InvalidRefreshTokenError
        return TokenPair(self._tokens.issue(user_id), new_refresh)

    def logout(self, refresh_token: str | None) -> None:
        """Завершает сессию. Без токена или с неизвестным ничего не делает."""
        if refresh_token:
            self._refresh_tokens.revoke(refresh_token)

    def authenticate(self, token: str) -> User:
        """Пользователь по токену; `AuthenticationError`, если токен не годится."""
        user = self._users.get(self._tokens.verify(token))
        if user is None:
            raise InvalidTokenError
        return user
