from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RefreshToken:
    """Запись о выданном refresh-токене. Сам токен не хранится, только его хеш.

    Токены одной сессии образуют цепочку (`family_id`): при каждом обновлении
    старый токен отзывается и выдаётся новый с тем же `family_id`.
    """

    id: int
    user_id: int
    family_id: str
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None = None

    def __repr__(self) -> str:
        return f"RefreshToken(id={self.id!r}, user_id={self.user_id!r})"
