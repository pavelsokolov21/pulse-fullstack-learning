from fastapi import Response

from pulse.api.settings import ApiSettings

REFRESH_COOKIE_NAME = "refresh_token"
# Cookie уходит только на ручки /auth: на остальные запросы браузер её не шлёт.
REFRESH_COOKIE_PATH = "/auth"


def set_refresh_cookie(
    response: Response, token: str, *, max_age: int, secure: bool
) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=max_age,
        path=REFRESH_COOKIE_PATH,
        httponly=True,  # недоступна из JavaScript: XSS не украдёт refresh
        secure=secure,  # только по HTTPS (в prod)
        samesite="strict",  # не уходит с чужих сайтов: защита /auth/refresh от CSRF
    )


def clear_refresh_cookie(response: Response, *, secure: bool) -> None:
    # Атрибуты должны совпадать с теми, с которыми cookie ставилась, иначе
    # браузер сочтёт это другой cookie и не удалит.
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite="strict",
    )


def cookie_secure(settings: ApiSettings) -> bool:
    # Secure-cookie уходит только по HTTPS; в dev по http://localhost её не было бы.
    return settings.environment == "prod"
