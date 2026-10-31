import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from pydantic import SecretStr

from pulse.domain.errors import (
    AuthenticationError,
    InvalidTokenError,
    TokenExpiredError,
)
from pulse.services.tokens import TokenService

SECRET = SecretStr("unit-test-secret-which-is-long-enough!!")
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
TTL = timedelta(minutes=15)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def tokens(clock: Clock) -> TokenService:
    return TokenService(SECRET, TTL, clock)


def claims_of(token: str) -> dict[str, Any]:
    # Подпись не проверяем: смотрим, что реально лежит в токене.
    return jwt.decode(token, options={"verify_signature": False})  # pyright: ignore[reportUnknownMemberType]


def forge(
    claims: dict[str, Any], key: str = SECRET.get_secret_value(), alg: str = "HS256"
) -> str:
    return jwt.encode(claims, key, algorithm=alg)  # pyright: ignore[reportUnknownMemberType]


def b64(data: dict[str, Any]) -> str:
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# --- выдача ----------------------------------------------------------------


def test_issue_puts_sub_iat_and_exp_in_the_token(tokens: TokenService) -> None:
    issued = tokens.issue(42)

    assert claims_of(issued.token) == {
        "sub": "42",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + TTL).timestamp()),
    }


def test_issue_reports_lifetime_in_seconds(tokens: TokenService) -> None:
    assert tokens.issue(1).expires_in == 900


def test_token_contains_nothing_but_the_three_claims(tokens: TokenService) -> None:
    assert set(claims_of(tokens.issue(1).token)) == {"sub", "iat", "exp"}


def test_token_header_pins_hs256(tokens: TokenService) -> None:
    header = jwt.get_unverified_header(tokens.issue(1).token)

    assert header["alg"] == "HS256"


def test_issue_uses_the_given_ttl(clock: Clock) -> None:
    service = TokenService(SECRET, timedelta(hours=1), clock)

    issued = service.issue(1)

    assert issued.expires_in == 3600
    assert claims_of(issued.token)["exp"] - claims_of(issued.token)["iat"] == 3600


def test_tokens_issued_at_different_times_differ(
    tokens: TokenService, clock: Clock
) -> None:
    first = tokens.issue(1).token
    clock.now += timedelta(seconds=1)

    assert tokens.issue(1).token != first


# --- проверка --------------------------------------------------------------


def test_verify_returns_the_user_id(tokens: TokenService) -> None:
    assert tokens.verify(tokens.issue(42).token) == 42


def test_token_is_valid_until_the_last_second(
    tokens: TokenService, clock: Clock
) -> None:
    token = tokens.issue(7).token
    clock.now = NOW + TTL - timedelta(seconds=1)

    assert tokens.verify(token) == 7


@pytest.mark.parametrize("extra", [0, 1, 3600])
def test_token_is_expired_at_exp_and_after(
    tokens: TokenService, clock: Clock, extra: int
) -> None:
    token = tokens.issue(7).token
    clock.now = NOW + TTL + timedelta(seconds=extra)

    with pytest.raises(TokenExpiredError):
        tokens.verify(token)


def test_expired_error_is_an_invalid_token_and_authentication_error() -> None:
    assert issubclass(TokenExpiredError, InvalidTokenError)
    assert issubclass(InvalidTokenError, AuthenticationError)


def test_token_signed_with_another_secret_is_rejected(tokens: TokenService) -> None:
    other = forge(
        {"sub": "1", "iat": 0, "exp": 9999999999},
        key="another-secret-another-secret-123456",
    )

    with pytest.raises(InvalidTokenError) as exc_info:
        tokens.verify(other)

    assert not isinstance(exc_info.value, TokenExpiredError)


def test_token_with_tampered_payload_is_rejected(tokens: TokenService) -> None:
    header, _, signature = tokens.issue(1).token.split(".")
    payload = b64({"sub": "2", "iat": 0, "exp": 9999999999})

    with pytest.raises(InvalidTokenError):
        tokens.verify(f"{header}.{payload}.{signature}")


def test_token_with_tampered_signature_is_rejected(tokens: TokenService) -> None:
    token = tokens.issue(1).token
    broken = token[:-2] + ("AA" if not token.endswith("AA") else "BB")

    with pytest.raises(InvalidTokenError):
        tokens.verify(broken)


def test_alg_none_token_is_rejected(tokens: TokenService) -> None:
    payload = {"sub": "1", "iat": 0, "exp": 9999999999}
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}."

    with pytest.raises(InvalidTokenError):
        tokens.verify(unsigned)


def test_token_with_another_algorithm_is_rejected(tokens: TokenService) -> None:
    other_alg = forge(
        {"sub": "1", "iat": 0, "exp": 9999999999},
        key=SECRET.get_secret_value() * 2,  # HS512 просит ключ не короче 64 байт
        alg="HS512",
    )

    with pytest.raises(InvalidTokenError):
        tokens.verify(other_alg)


@pytest.mark.parametrize(
    "garbage", ["", "x", "a.b", "a.b.c", "Bearer abc", " ", "....", "ы.ы.ы"]
)
def test_garbage_is_rejected(tokens: TokenService, garbage: str) -> None:
    with pytest.raises(InvalidTokenError):
        tokens.verify(garbage)


@pytest.mark.parametrize("missing", ["sub", "iat", "exp"])
def test_token_without_required_claim_is_rejected(
    tokens: TokenService, missing: str
) -> None:
    claims: dict[str, Any] = {"sub": "1", "iat": 0, "exp": 9999999999}
    del claims[missing]

    with pytest.raises(InvalidTokenError):
        tokens.verify(forge(claims))


@pytest.mark.parametrize("sub", [1, "abc", "", "1.5", None])
def test_token_with_bad_subject_is_rejected(tokens: TokenService, sub: Any) -> None:
    claims: dict[str, Any] = {"sub": sub, "iat": 0, "exp": 9999999999}

    with pytest.raises(InvalidTokenError):
        tokens.verify(forge(claims))


def test_verification_uses_the_injected_clock_not_system_time(clock: Clock) -> None:
    # Токен «из 2026 года» валиден в 2026-м по часам сервиса, хотя системное
    # время уже другое. Если бы проверку делал PyJWT, он бы отверг его как истёкший.
    service = TokenService(SECRET, TTL, clock)

    assert service.verify(service.issue(5).token) == 5


def test_secret_is_hidden_in_repr_of_secretstr() -> None:
    assert "unit-test-secret" not in repr(SECRET)
    assert "unit-test-secret" not in str(SECRET)
