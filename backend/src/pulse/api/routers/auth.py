from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from pulse.api.cookies import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    cookie_secure,
    set_refresh_cookie,
)
from pulse.api.deps import (
    AuthServiceDep,
    CurrentUserDep,
    SettingsDep,
    enforce_login_rate_limit,
)
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.auth import RegisterRequest, TokenResponse, UserRead
from pulse.domain.errors import MissingTokenError
from pulse.services.auth import TokenPair

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse}
    },
)

UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}
}
TOO_MANY_REQUESTS: dict[int | str, dict[str, Any]] = {
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "model": ErrorResponse,
        "headers": {"Retry-After": {"schema": {"type": "integer"}}},
    }
}

RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)]


def _respond_with_tokens(
    pair: TokenPair, response: Response, settings: SettingsDep
) -> TokenResponse:
    """Access-токен идёт в теле ответа, refresh только в httpOnly-cookie."""
    set_refresh_cookie(
        response,
        pair.refresh.token,
        max_age=pair.refresh.expires_in,
        secure=cookie_secure(settings),
    )
    # RFC 6749 §5.1: ответ с токеном нельзя кэшировать.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(
        access_token=pair.access.token, expires_in=pair.access.expires_in
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def register(body: RegisterRequest, auth: AuthServiceDep) -> UserRead:
    user = await auth.register(body.email, body.password.get_secret_value())
    return UserRead.model_validate(user)


@router.post(
    "/login",
    dependencies=[Depends(enforce_login_rate_limit)],
    responses=UNAUTHORIZED | TOO_MANY_REQUESTS,
)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    auth: AuthServiceDep,
    response: Response,
    settings: SettingsDep,
) -> TokenResponse:
    # OAuth2 password flow: поле называется username, в нём лежит email.
    pair = await auth.login(form.username, form.password)
    return _respond_with_tokens(pair, response, settings)


@router.post("/refresh", responses=UNAUTHORIZED)
async def refresh(
    auth: AuthServiceDep,
    response: Response,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> TokenResponse:
    """Меняет refresh-cookie на новую пару токенов (ротация)."""
    if not refresh_token:
        raise MissingTokenError
    pair = auth.refresh(refresh_token)
    return _respond_with_tokens(pair, response, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    auth: AuthServiceDep,
    response: Response,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> None:
    """Отзывает сессию и сбрасывает cookie. Повторный вызов тоже даёт 204."""
    auth.logout(refresh_token)
    clear_refresh_cookie(response, secure=cookie_secure(settings))


@router.get("/me", responses=UNAUTHORIZED)
async def me(user: CurrentUserDep) -> UserRead:
    return UserRead.model_validate(user)
