from pulse.core.passwords import PasswordHasher
from pulse.core.utils.normalize_email import normalize_email
from pulse.domain.errors import (
    DuplicateUserError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from pulse.domain.users import MAX_PASSWORD_LENGTH, User
from pulse.repositories.users import UserRepository
from pulse.services.tokens import AccessToken, TokenService


class AuthService:
    """Регистрация, вход по паролю и проверка токена."""

    def __init__(
        self, users: UserRepository, hasher: PasswordHasher, tokens: TokenService
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._tokens = tokens

    async def register(self, email: str, password: str) -> User:
        email = normalize_email(email)
        password_hash = await self._hasher.hash(password)

        # Проверка стоит после `await`, прямо перед созданием, без await между
        # ними: пока хешировался пароль, такой же email мог зарегистрироваться
        # параллельным запросом. В базе это закроет UNIQUE-ограничение.
        if self._users.find_by_email(email) is not None:
            raise DuplicateUserError
        return self._users.create(email=email, password_hash=password_hash)

    async def login(self, email: str, password: str) -> AccessToken:
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
        return self._tokens.issue(user.id)

    def authenticate(self, token: str) -> User:
        """Пользователь по токену; `AuthenticationError`, если токен не годится."""
        user = self._users.get(self._tokens.verify(token))
        if user is None:
            raise InvalidTokenError
        return user
