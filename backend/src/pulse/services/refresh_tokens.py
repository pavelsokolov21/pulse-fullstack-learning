import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from pulse.core.clock import Clock, utc_now
from pulse.domain.errors import InvalidRefreshTokenError, RefreshTokenExpiredError
from pulse.repositories.refresh_tokens import RefreshTokenRepository


@dataclass(frozen=True)
class IssuedRefreshToken:
    token: str  # сырое значение: показывается клиенту один раз
    expires_in: int  # секунды


def hash_token(token: str) -> str:
    # Токен случайный и длинный, поэтому достаточно быстрого SHA-256: медленный
    # argon2 нужен только для паролей, которые люди выбирают сами.
    return hashlib.sha256(token.encode()).hexdigest()


class RefreshTokenService:
    """Выдача, ротация и отзыв refresh-токенов.

    Токен непрозрачный (случайная строка, не JWT), в хранилище лежит только его
    хеш: утечка базы не даёт готовых токенов. При каждом обновлении старый токен
    отзывается и выдаётся новый в той же цепочке. Если приходит уже отозванный
    токен, значит им воспользовались дважды (один из пользователей — вор), и
    вся цепочка отзывается: законному владельцу придётся войти заново.
    """

    def __init__(
        self,
        repository: RefreshTokenRepository,
        ttl: timedelta,
        clock: Clock = utc_now,
    ) -> None:
        self._repository = repository
        self._ttl = ttl
        self._clock = clock

    def issue(self, user_id: int) -> IssuedRefreshToken:
        """Новая сессия: токен в новой цепочке."""
        return self._create(user_id, family_id=uuid.uuid4().hex)

    def rotate(self, token: str) -> tuple[int, IssuedRefreshToken]:
        """Меняет токен на новый; возвращает id пользователя и новый токен."""
        record = self._repository.find_by_hash(hash_token(token))
        if record is None:
            raise InvalidRefreshTokenError

        now = self._clock()
        if record.revoked_at is not None:
            # Повторное использование: отзываем всю цепочку.
            self._repository.revoke_family(record.family_id, now)
            raise InvalidRefreshTokenError
        if now >= record.expires_at:
            raise RefreshTokenExpiredError

        # Между проверкой и отзывом нет await: параллельный запрос с тем же токеном
        # не успеет проскочить. В базе это UPDATE ... WHERE revoked_at IS NULL.
        self._repository.revoke(record.id, now)
        return record.user_id, self._create(record.user_id, record.family_id)

    def revoke(self, token: str) -> None:
        """Завершает сессию: отзывает всю цепочку. Неизвестный токен игнорируется."""
        record = self._repository.find_by_hash(hash_token(token))
        if record is not None:
            self._repository.revoke_family(record.family_id, self._clock())

    def _create(self, user_id: int, family_id: str) -> IssuedRefreshToken:
        token = secrets.token_urlsafe(32)
        self._repository.add(
            user_id=user_id,
            family_id=family_id,
            token_hash=hash_token(token),
            expires_at=self._clock() + self._ttl,
        )
        return IssuedRefreshToken(token, int(self._ttl.total_seconds()))
