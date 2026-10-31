from dataclasses import dataclass

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


@dataclass(frozen=True)
class User:
    id: int
    email: str
    password_hash: str

    def __repr__(self) -> str:
        # Хеш не должен попадать в логи и трассировки, даже случайно.
        return f"User(id={self.id!r}, email={self.email!r})"
