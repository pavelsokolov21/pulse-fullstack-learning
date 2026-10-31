from fastapi import Response

from pulse.api.settings import ApiSettings

REFRESH_COOKIE_NAME = "refresh_token"


def set_refresh_cookie(
    response: Response, token: str, *, max_age: int, settings: ApiSettings
) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=max_age,
        # Cookie уходит только на ручки /auth: на остальные запросы браузер её не шлёт.
        path=settings.refresh_cookie_path,
        httponly=True,  # недоступна из JavaScript: XSS не украдёт refresh
        secure=cookie_secure(settings),  # только по HTTPS (в prod)
        samesite="strict",  # не уходит с чужих сайтов: защита /auth/refresh от CSRF
    )


def clear_refresh_cookie(response: Response, settings: ApiSettings) -> None:
    # Атрибуты должны совпадать с теми, с которыми cookie ставилась, иначе
    # браузер сочтёт это другой cookie и не удалит.
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=cookie_secure(settings),
        samesite="strict",
    )


def cookie_secure(settings: ApiSettings) -> bool:
    # Secure-cookie уходит только по HTTPS; в dev по http://localhost её не было бы.
    return settings.environment == "prod"
