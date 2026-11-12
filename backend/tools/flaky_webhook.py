"""Капризный вебхук для опытов с повторами задач Celery.

Первые N запросов отвечает ошибкой, остальные принимает. В журнале видно время каждой
попытки, то есть рост пауз между повторами:

    uv run python tools/flaky_webhook.py --fail 3 --status 503
    PULSE_WORKER_NOTIFY_WEBHOOK_URL=http://127.0.0.1:9100/hook uv run poe worker

Сервис учебный: слушает только 127.0.0.1 и ничего не сохраняет.
"""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def make_handler(
    fail: int, status: int, retry_after: int | None
) -> type[BaseHTTPRequestHandler]:
    started = time.monotonic()
    attempts = 0

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal attempts
            attempts += 1
            length = int(self.headers.get("Content-Length", "0"))
            body: dict[str, Any] = json.loads(self.rfile.read(length) or b"{}")
            failing = attempts <= fail
            code = status if failing else 200
            print(
                f"[{time.monotonic() - started:7.1f} с] попытка {attempts}: "
                f"{code}  check_id={body.get('check_id')}  {body.get('text', '')}",
                flush=True,
            )
            self.send_response(code)
            if failing and retry_after is not None:
                self.send_header("Retry-After", str(retry_after))
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return  # свой журнал выше

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Капризный вебхук")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--fail", type=int, default=3, help="сколько запросов отказать")
    parser.add_argument("--status", type=int, default=503, help="код отказа")
    parser.add_argument(
        "--retry-after", type=int, default=None, help="секунды в Retry-After"
    )
    args = parser.parse_args()
    server = HTTPServer(
        ("127.0.0.1", args.port), make_handler(args.fail, args.status, args.retry_after)
    )
    print(
        f"Слушаю 127.0.0.1:{args.port}: первые {args.fail} запросов получат "
        f"{args.status}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
