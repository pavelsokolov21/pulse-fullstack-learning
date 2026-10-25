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

```
src/pulse/
  cli.py         команда `pulse check`, форматирование вывода
  settings.py    настройки из окружения (pydantic-settings)
  monitors/      модель монитора и загрузка из TOML
  checks/        проверка URL, пачки, скользящее окно
  decorators/    валидация аргументов
  utils/         разбор интервалов, нормализация URL, p95, статистика, таймер
tests/           зеркалит структуру src/pulse/
```
