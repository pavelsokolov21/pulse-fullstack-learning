# Pulse: backend

Монитор доступности сайтов. Сейчас это CLI: читает список мониторов из TOML-файла, асинхронно проверяет URL и печатает таблицу или JSON.

## Запуск

Нужны Python 3.12+ и [uv](https://docs.astral.sh/uv/). Команды выполняются из `backend/`.

```bash
uv sync                                   # установить зависимости
uv run pulse check monitors.toml          # таблица
uv run pulse check monitors.toml --json   # JSON
uv run pulse --help
```

Пример вывода:

```
STATUS  CODE  MS    URL                      ERROR
OK      200   112   https://example.com
FAIL    -     3000  https://down.example     Network Error: ConnectError
```

Код выхода: `0` все сайты доступны, `1` есть недоступные, `2` ошибка конфигурации (файл, TOML, поля монитора, `PULSE_*`). Это удобно для скриптов и CI.

## Файл мониторов

```toml
[[monitors]]
url = "example.com"   # схема https:// добавится сама
interval = "5m"       # число и единица: s, m, h
timeout = 10          # секунды, необязательно (по умолчанию 10)
```

Неизвестные ключи и неверные значения это ошибка: файл не применяется частично.

## Настройки через окружение

| Переменная          | По умолчанию | Смысл                                | Ограничение |
| ------------------- | ------------ | ------------------------------------ | ----------- |
| `PULSE_CONCURRENCY` | `10`         | сколько проверок идёт одновременно   | целое, >= 1 |
| `PULSE_RETRIES`     | `2`          | число повторов после сетевого сбоя   | целое, >= 0 |
| `PULSE_RETRY_DELAY` | `0.5`        | пауза между повторами, секунды       | число, >= 0 |

```bash
PULSE_CONCURRENCY=50 PULSE_RETRIES=0 uv run pulse check monitors.toml
```

Повторяются только сетевые сбои (таймаут, отказ соединения, DNS). Ответ сервера, даже 503, считается результатом проверки и не повторяется.

## Проверки качества

```bash
uv run poe check    # ruff + pyright (strict) + pytest
```

По отдельности: `uv run poe lint`, `uv run poe types`, `uv run poe test`, `uv run poe watch` (тесты при изменении файлов).

## Структура

Слои зависят только вниз: `api → services → repositories → domain`. `core` не зависит ни от кого, `checks` и `cli` стоят сбоку. Направление импортов проверяет `tests/test_architecture.py`.

```
src/pulse/
  api/            HTTP: сборка приложения, ошибки, Depends
    middlewares/  ASGI-middleware (request-id)
    routers/      обработчики эндпоинтов
    schemas/      тела запросов и ответов (Create / Update / Read)
  services/       операции над мониторами, бизнес-правила
  repositories/   хранение (сейчас в памяти, в фазе 2 PostgreSQL)
  domain/         сущности, общие типы с валидацией, доменные ошибки
  checks/         проверка URL: runner, скользящее окно, статистика
  cli/            команда `pulse check`, загрузка мониторов из TOML
  core/           настройки, утилиты, декораторы, контекст запроса для логов
tests/            зеркалит структуру src/pulse/
```

Запуск API: `uv run poe dev`, затем http://127.0.0.1:8000/docs.
