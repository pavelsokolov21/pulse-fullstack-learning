import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from pulse import cli
from pulse.cli import app

runner = CliRunner()

TWO_MONITORS = """
[[monitors]]
url = "good.test"
interval = "5m"

[[monitors]]
url = "bad.test"
interval = "30s"
"""

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CONCURRENCY", "RETRIES", "RETRY_DELAY"):
        monkeypatch.delenv(f"PULSE_{name}", raising=False)
    # Без паузы между повторами тесты не ждут по полсекунды.
    monkeypatch.setenv("PULSE_RETRY_DELAY", "0")


class Network:
    """Подменяет create_client клиентом на MockTransport и запоминает вызовы."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []

        def recording(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        def create_client() -> httpx.AsyncClient:
            client = httpx.AsyncClient(transport=httpx.MockTransport(recording))
            self.clients.append(client)
            return client

        monkeypatch.setattr("pulse.cli.create_client", create_client)


def by_host(request: httpx.Request) -> httpx.Response:
    if request.url.host == "bad.test":
        return httpx.Response(503)
    return httpx.Response(200)


def write(tmp_path: Path, content: str = TWO_MONITORS) -> Path:
    path = tmp_path / "monitors.toml"
    path.write_text(content, encoding="utf-8")
    return path


# --- успешные проверки -----------------------------------------------------


def test_all_monitors_up_exits_zero_and_prints_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    Network(monkeypatch, lambda _: httpx.Response(200))

    result = runner.invoke(app, ["check", str(write(tmp_path))])

    assert result.exit_code == 0
    assert "STATUS" in result.stdout
    assert "https://good.test" in result.stdout
    assert "https://bad.test" in result.stdout


def test_failed_monitor_exits_with_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    Network(monkeypatch, by_host)

    result = runner.invoke(app, ["check", str(write(tmp_path))])

    assert result.exit_code == cli.EXIT_CHECK_FAILED == 1
    # Таблица печатается и при ошибках: по ней видно, что именно упало.
    assert "503" in result.stdout


def test_unreachable_site_counts_as_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    Network(monkeypatch, refuse)

    result = runner.invoke(app, ["check", str(write(tmp_path))])

    assert result.exit_code == 1
    assert "Network Error: ConnectError" in result.stdout


def test_any_2xx_status_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    Network(monkeypatch, lambda _: httpx.Response(204))

    assert runner.invoke(app, ["check", str(write(tmp_path))]).exit_code == 0


# --- --json ----------------------------------------------------------------


def test_json_flag_prints_machine_readable_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    Network(monkeypatch, by_host)

    result = runner.invoke(app, ["check", str(write(tmp_path)), "--json"])

    items = json.loads(result.stdout)
    assert [(i["url"], i["ok"]) for i in items] == [
        ("https://good.test", True),
        ("https://bad.test", False),
    ]
    assert "STATUS" not in result.stdout
    # Код выхода зависит от результатов, а не от формата вывода.
    assert result.exit_code == 1


def test_results_follow_config_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    Network(monkeypatch, lambda _: httpx.Response(200))

    result = runner.invoke(app, ["check", str(write(tmp_path))])

    assert result.stdout.index("good.test") < result.stdout.index("bad.test")


# --- настройки -------------------------------------------------------------


def test_settings_retries_are_passed_to_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    network = Network(monkeypatch, refuse)
    monkeypatch.setenv("PULSE_RETRIES", "0")
    content = '[[monitors]]\nurl = "a.test"\ninterval = "1m"\n'

    runner.invoke(app, ["check", str(write(tmp_path, content))])

    assert len(network.requests) == 1


def test_default_retries_make_three_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    network = Network(monkeypatch, refuse)
    content = '[[monitors]]\nurl = "a.test"\ninterval = "1m"\n'

    runner.invoke(app, ["check", str(write(tmp_path, content))])

    assert len(network.requests) == 3


def test_client_is_closed_after_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    network = Network(monkeypatch, lambda _: httpx.Response(200))

    runner.invoke(app, ["check", str(write(tmp_path))])

    assert [c.is_closed for c in network.clients] == [True]


# --- ошибки конфигурации ---------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "",
        "[[monitors\nurl = 1\n",
        '[[monitors]]\nurl = "a.test"\ninterval = "0s"\n',
        '[[monitors]]\nurl = "a.test"\n',
    ],
    ids=["no-monitors", "broken-toml", "invalid-monitor", "missing-field"],
)
def test_bad_config_exits_with_two_and_does_not_touch_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    network = Network(monkeypatch, lambda _: httpx.Response(200))

    result = runner.invoke(app, ["check", str(write(tmp_path, content))])

    assert result.exit_code == cli.EXIT_CONFIG_ERROR == 2
    assert "Ошибка конфигурации" in result.stderr
    assert result.stdout == ""
    assert network.requests == []


@pytest.mark.parametrize(
    ("name", "value"),
    [("CONCURRENCY", "0"), ("RETRIES", "-1"), ("RETRY_DELAY", "soon")],
)
def test_invalid_environment_exits_with_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, value: str
) -> None:
    network = Network(monkeypatch, lambda _: httpx.Response(200))
    monkeypatch.setenv(f"PULSE_{name}", value)

    result = runner.invoke(app, ["check", str(write(tmp_path))])

    assert result.exit_code == 2
    assert "Ошибка конфигурации" in result.stderr
    assert network.requests == []


def test_missing_file_exits_with_two(tmp_path: Path) -> None:
    # Это проверка самого typer (exists=True), а не нашего кода.
    result = runner.invoke(app, ["check", str(tmp_path / "nope.toml")])

    assert result.exit_code == 2


def test_missing_argument_exits_with_two() -> None:
    assert runner.invoke(app, ["check"]).exit_code == 2


def test_help_mentions_json_flag() -> None:
    result = runner.invoke(app, ["check", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.stdout
