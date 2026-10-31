from dataclasses import replace
from datetime import datetime
from itertools import count

from pulse.domain.refresh_tokens import RefreshToken


class RefreshTokenRepository:
    """Refresh-токены в памяти (до PostgreSQL в фазе 2).

    Ищет по хешу токена: сырое значение знает только клиент. Просроченные и
    отозванные записи не удаляются (нужны, чтобы заметить повторное
    использование); в базе их чистит периодическая задача.
    """

    def __init__(self) -> None:
        self._tokens: dict[int, RefreshToken] = {}
        self._ids = count(1)

    def add(
        self, *, user_id: int, family_id: str, token_hash: str, expires_at: datetime
    ) -> RefreshToken:
        token = RefreshToken(
            next(self._ids), user_id, family_id, token_hash, expires_at
        )
        self._tokens[token.id] = token
        return token

    def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        return next(
            (t for t in self._tokens.values() if t.token_hash == token_hash), None
        )

    def revoke(self, token_id: int, at: datetime) -> None:
        token = self._tokens[token_id]
        if token.revoked_at is None:
            self._tokens[token_id] = replace(token, revoked_at=at)

    def revoke_family(self, family_id: str, at: datetime) -> None:
        for token in list(self._tokens.values()):
            if token.family_id == family_id:
                self.revoke(token.id, at)

    def list_family(self, family_id: str) -> list[RefreshToken]:
        return [t for t in self._tokens.values() if t.family_id == family_id]
