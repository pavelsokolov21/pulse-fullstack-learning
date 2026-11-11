from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pulse.api.deps import get_login_rate_limiter, get_password_hasher
from pulse.api.main import create_app
from pulse.api.settings import ApiSettings
from pulse.core.passwords import PasswordHasher
from pulse.core.rate_limit import InMemoryRateLimiter, SlidingWindowRateLimiter

PASSWORD = "correct horse battery"
ATTEMPTS = 3
WINDOW = 60


class MonotonicClock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


class CountingHasher(PasswordHasher):
    def __init__(self, inner: PasswordHasher) -> None:
        self.inner = inner
        self.verify_calls = 0

    async def hash(self, password: str) -> str:
        return await self.inner.hash(password)

    async def verify(self, password: str, hashed: str) -> bool:
        self.verify_calls += 1
        return await self.inner.verify(password, hashed)

    async def verify_dummy(self, password: str) -> None:
        self.verify_calls += 1
        await self.inner.verify_dummy(password)


@pytest.fixture
def tick() -> MonotonicClock:
    return MonotonicClock()


@pytest.fixture
def counting(auth_app: FastAPI) -> CountingHasher:
    original = auth_app.dependency_overrides[get_password_hasher]()
    hasher = CountingHasher(original)
    auth_app.dependency_overrides[get_password_hasher] = lambda: hasher
    return hasher


@pytest.fixture
def limited_client(
    auth_app: FastAPI, tick: MonotonicClock, counting: CountingHasher
) -> Iterator[TestClient]:
    # Управляемые часы нужны этим тестам, поэтому здесь локальный лимитер. Настоящий
    # Redis-лимитер проверяют `test_default_limit_comes_from_settings` ниже и
    # tests/repositories/test_redis_rate_limiter.py.
    limiter = InMemoryRateLimiter(
        SlidingWindowRateLimiter(ATTEMPTS, WINDOW, clock=tick)
    )
    auth_app.dependency_overrides[get_login_rate_limiter] = lambda: limiter
    with TestClient(auth_app) as client:
        client.post(
            "/auth/register",
            json={"email": "alice@example.com", "password": PASSWORD},
        )
        yield client


def login(
    client: TestClient, password: str = PASSWORD, email: str = "alice@example.com"
):
    return client.post("/auth/login", data={"username": email, "password": password})


def test_attempts_within_the_limit_are_processed_normally(
    limited_client: TestClient,
) -> None:
    statuses = [
        login(limited_client, "wrong password").status_code for _ in range(ATTEMPTS)
    ]

    assert statuses == [401] * ATTEMPTS


def test_attempt_over_the_limit_returns_429_with_retry_after(
    limited_client: TestClient,
) -> None:
    for _ in range(ATTEMPTS):
        login(limited_client, "wrong password")

    response = login(limited_client, "wrong password")

    assert response.status_code == 429
    assert response.headers["retry-after"] == str(WINDOW)
    assert response.json() == {
        "code": "rate_limited",
        "message": "Слишком много запросов, попробуйте позже",
    }


def test_correct_password_is_also_blocked_while_limited(
    limited_client: TestClient,
) -> None:
    for _ in range(ATTEMPTS):
        login(limited_client, "wrong password")

    response = login(limited_client)

    assert response.status_code == 429
    assert "access_token" not in response.text
    assert "set-cookie" not in response.headers


def test_successful_logins_count_too(limited_client: TestClient) -> None:
    statuses = [login(limited_client).status_code for _ in range(ATTEMPTS + 1)]

    assert statuses == [200] * ATTEMPTS + [429]


def test_blocked_attempt_does_not_reach_password_hashing(
    limited_client: TestClient, counting: CountingHasher
) -> None:
    for _ in range(ATTEMPTS):
        login(limited_client, "wrong password")
    before = counting.verify_calls

    for _ in range(5):
        login(limited_client, "wrong password")

    assert counting.verify_calls == before


def test_retry_after_counts_down(
    limited_client: TestClient, tick: MonotonicClock
) -> None:
    for _ in range(ATTEMPTS):
        login(limited_client)
    tick.now += 20

    response = login(limited_client)

    assert response.headers["retry-after"] == "40"


def test_login_works_again_after_the_window(
    limited_client: TestClient, tick: MonotonicClock
) -> None:
    for _ in range(ATTEMPTS):
        login(limited_client)
    assert login(limited_client).status_code == 429

    tick.now += WINDOW

    assert login(limited_client).status_code == 200


def test_waiting_less_than_retry_after_does_not_help(
    limited_client: TestClient, tick: MonotonicClock
) -> None:
    for _ in range(ATTEMPTS):
        login(limited_client)
    tick.now += WINDOW - 1

    assert login(limited_client).status_code == 429


def test_unknown_email_attempts_are_limited_too(limited_client: TestClient) -> None:
    statuses = [
        login(limited_client, email=f"ghost{n}@example.com").status_code
        for n in range(ATTEMPTS + 1)
    ]

    assert statuses == [401] * ATTEMPTS + [429]


def test_each_client_address_has_its_own_budget(
    auth_app: FastAPI, limited_client: TestClient
) -> None:
    for _ in range(ATTEMPTS):
        login(limited_client)
    assert login(limited_client).status_code == 429

    # С другого адреса тот же вход обработан (200), а не отвергнут лимитом (429).
    with TestClient(auth_app, client=("203.0.113.7", 50000)) as other:
        assert login(other).status_code == 200


def test_x_forwarded_for_header_cannot_dodge_the_limit(
    limited_client: TestClient,
) -> None:
    for _ in range(ATTEMPTS):
        login(limited_client)

    response = limited_client.post(
        "/auth/login",
        data={"username": "alice@example.com", "password": PASSWORD},
        headers={"X-Forwarded-For": "198.51.100.99"},
    )

    assert response.status_code == 429


def test_only_login_is_limited(limited_client: TestClient) -> None:
    for _ in range(ATTEMPTS + 1):
        login(limited_client)

    assert limited_client.get("/auth/me").status_code == 401  # не 429
    assert limited_client.post("/auth/refresh").status_code != 429
    assert limited_client.post("/auth/logout").status_code == 204
    register = limited_client.post(
        "/auth/register", json={"email": "bob@example.com", "password": PASSWORD}
    )
    assert register.status_code == 201


def test_429_response_has_a_request_id(limited_client: TestClient) -> None:
    for _ in range(ATTEMPTS):
        login(limited_client)

    assert len(login(limited_client).headers["x-request-id"]) == 32


def test_openapi_documents_429_with_retry_after(limited_client: TestClient) -> None:
    response = limited_client.get("/openapi.json").json()["paths"]["/auth/login"][
        "post"
    ]["responses"]["429"]

    assert "Retry-After" in response["headers"]


def test_default_limit_comes_from_settings(auth_app: FastAPI) -> None:
    # Без подмены лимитёра действуют значения ApiSettings: 5 попыток за 60 секунд.
    auth_app.dependency_overrides.pop(get_login_rate_limiter)

    with TestClient(auth_app) as client:
        statuses = [login(client, "wrong password").status_code for _ in range(6)]

    assert statuses == [401] * 5 + [429]


def test_limit_values_are_taken_from_settings() -> None:
    # Значения не совпадают с умолчаниями (5 за 60 с): лимитёр собран из настроек.
    overrides: dict[str, Any] = {
        "login_rate_limit_attempts": 2,
        "login_rate_limit_window_seconds": 30,
    }
    app = create_app(ApiSettings(**overrides))

    with TestClient(app) as client:
        responses = [login(client, "wrong password") for _ in range(3)]

    assert [r.status_code for r in responses] == [401, 401, 429]
    assert 1 <= int(responses[2].headers["retry-after"]) <= 30
