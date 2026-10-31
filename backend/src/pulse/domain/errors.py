class MonitorNotFoundError(Exception):
    """Монитора с таким id нет в хранилище."""

    def __init__(self, monitor_id: int) -> None:
        super().__init__(f"Монитор {monitor_id} не найден")
        self.monitor_id = monitor_id


class DuplicateMonitorError(Exception):
    """Монитор с таким URL уже есть."""

    def __init__(self, url: str, existing_id: int) -> None:
        super().__init__(f"Монитор с URL {url} уже существует (id {existing_id})")
        self.url = url
        self.existing_id = existing_id


class DuplicateUserError(Exception):
    """Пользователь с таким email уже есть."""

    def __init__(self) -> None:
        super().__init__("Пользователь с таким email уже существует")


class AuthenticationError(Exception):
    """Базовый класс 401: запрос не удалось аутентифицировать.

    Тексты общие: по ним нельзя понять, чего именно не оказалось (пользователя
    или пароля).
    """

    code = "not_authenticated"
    message = "Требуется авторизация"

    def __init__(self) -> None:
        super().__init__(self.message)


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"
    message = "Неверный email или пароль"


class MissingTokenError(AuthenticationError):
    pass


class InvalidTokenError(AuthenticationError):
    """Токен подделан, повреждён или относится к несуществующему пользователю."""

    code = "invalid_token"
    message = "Недействительный токен"


class TokenExpiredError(InvalidTokenError):
    code = "token_expired"
    message = "Срок действия токена истёк"


class RefreshTokenError(AuthenticationError):
    """Базовый класс ошибок refresh-токена: при них cookie нужно сбросить."""

    code = "invalid_refresh_token"
    message = "Недействительный refresh-токен"


class InvalidRefreshTokenError(RefreshTokenError):
    """Токен неизвестен, отозван или использован повторно."""


class RefreshTokenExpiredError(RefreshTokenError):
    code = "refresh_token_expired"
    message = "Срок действия refresh-токена истёк"


class PermissionDeniedError(Exception):
    """Пользователь аутентифицирован, но его роль не разрешает это действие (403)."""

    def __init__(self) -> None:
        super().__init__("Недостаточно прав для этого действия")


class RateLimitExceededError(Exception):
    """Слишком много запросов; повторить можно через `retry_after` секунд (429)."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("Слишком много запросов, попробуйте позже")
        self.retry_after = retry_after
