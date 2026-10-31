from dataclasses import dataclass
from enum import StrEnum

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class Role(StrEnum):
    """Какие действия разрешены (глаголы). С какими объектами, решает владение."""

    ADMIN = "admin"
    MANAGER = "manager"
    VIEWER = "viewer"


@dataclass(frozen=True)
class User:
    id: int
    email: str
    password_hash: str
    role: Role = Role.MANAGER

    def __repr__(self) -> str:
        # Хеш не должен попадать в логи и трассировки, даже случайно.
        return f"User(id={self.id!r}, email={self.email!r}, role={self.role!r})"
