from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from pulse.api.deps import AuthServiceDep, CurrentUserDep
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.auth import RegisterRequest, TokenResponse, UserRead

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


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def register(body: RegisterRequest, auth: AuthServiceDep) -> UserRead:
    user = await auth.register(body.email, body.password.get_secret_value())
    return UserRead.model_validate(user)


@router.post("/login", responses=UNAUTHORIZED)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    auth: AuthServiceDep,
    response: Response,
) -> TokenResponse:
    # OAuth2 password flow: поле называется username, в нём лежит email.
    token = await auth.login(form.username, form.password)
    # RFC 6749 §5.1: ответ с токеном нельзя кэшировать.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=token.token, expires_in=token.expires_in)


@router.get("/me", responses=UNAUTHORIZED)
async def me(user: CurrentUserDep) -> UserRead:
    return UserRead.model_validate(user)
