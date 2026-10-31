import json
import os
import subprocess
import sys
from pathlib import Path

from pulse.api.export_openapi import build_schema, main, render_schema


def test_main_writes_the_schema_to_the_given_file(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    main([str(target)])

    assert json.loads(target.read_text(encoding="utf-8")) == build_schema()


def test_output_is_stable_and_diff_friendly() -> None:
    text = render_schema({"b": 1, "a": "Кириллица"})

    assert text == '{\n  "b": 1,\n  "a": "Кириллица"\n}\n'


def test_runs_without_server_and_without_a_secret(tmp_path: Path) -> None:
    # Свежий процесс без PULSE_API_JWT_SECRET: так скрипт запускает `npm run gen:api`.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PULSE_")}
    target = tmp_path / "openapi.json"

    result = subprocess.run(
        [sys.executable, "-m", "pulse.api.export_openapi", str(target)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(target.read_text(encoding="utf-8"))["info"]["title"] == "Pulse"


def test_placeholder_secret_does_not_leak_into_the_schema() -> None:
    assert "placeholder" not in render_schema(build_schema())
