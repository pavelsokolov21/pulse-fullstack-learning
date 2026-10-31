from dataclasses import dataclass
from datetime import timedelta

import jwt
from pydantic import SecretStr

from pulse.core.clock import Clock, utc_now
from pulse.domain.errors import InvalidTokenError, TokenExpiredError

ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ["sub", "iat", "exp"]


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_in: int  # секунды


class TokenService:
    """Выдача и проверка короткоживущих JWT (HS256) с claims `sub`, `iat`, `exp`.

    В payload нет ничего, кроме id пользователя: JWT только подписан, а не
    зашифрован, его содержимое читает любой.
    """

    def __init__(
        self, secret: SecretStr, ttl: timedelta, clock: Clock = utc_now
    ) -> None:
        self._secret = secret
        self._ttl = ttl
        self._clock = clock

    def issue(self, user_id: int) -> AccessToken:
        issued_at = int(self._clock().timestamp())
        expires_in = int(self._ttl.total_seconds())
        claims = {"sub": str(user_id), "iat": issued_at, "exp": issued_at + expires_in}
        # Тип параметра key в PyJWT ссылается на cryptography, которой нет (для HS256
        # она не нужна), поэтому pyright считает сигнатуру частично неизвестной.
        token = jwt.encode(  # pyright: ignore[reportUnknownMemberType]
            claims, self._secret.get_secret_value(), algorithm=ALGORITHM
        )
        return AccessToken(token, expires_in)

    def verify(self, token: str) -> int:
        """Возвращает id пользователя или бросает `InvalidTokenError`."""
        try:
            claims = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
                token,
                self._secret.get_secret_value(),
                # Алгоритм задан явно: токен не выбирает его сам (alg=none и подмена).
                algorithms=[ALGORITHM],
                # Срок проверяем сами по своим часам: PyJWT читает системное время,
                # и тесты не могли бы подставить своё.
                options={
                    "require": _REQUIRED_CLAIMS,
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError from exc

        if int(self._clock().timestamp()) >= claims["exp"]:
            raise TokenExpiredError

        try:
            return int(claims["sub"])
        except ValueError as exc:
            raise InvalidTokenError from exc
