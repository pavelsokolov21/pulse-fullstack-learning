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

Запуск API: нужен секрет подписи JWT (без него приложение не стартует).

```bash
export PULSE_API_JWT_SECRET=$(openssl rand -hex 32)   # не короче 32 символов
uv run poe dev                                         # http://127.0.0.1:8000/docs
```

## API

Все ручки `/monitors/...` требуют заголовок `Authorization: Bearer <токен>`.

| Метод и путь                     | Что делает                                      |
| -------------------------------- | ----------------------------------------------- |
| `POST /auth/register`            | регистрация: `{email, password}`, пароль 8–128 символов (201; 409, если email занят) |
| `POST /auth/login`               | вход по форме OAuth2 (`username` = email, `password`), выдаёт JWT (401 при ошибке) |
| `GET /auth/me`                   | текущий пользователь по токену                  |
| `POST /monitors`                 | создать монитор (201; 409, если URL уже есть)   |
| `GET /monitors`                  | список мониторов                                |
| `GET /monitors/{id}`             | один монитор (404, если нет)                    |
| `PATCH /monitors/{id}`           | изменить переданные поля                        |
| `DELETE /monitors/{id}`          | удалить монитор и его проверки (204)            |
| `POST /monitors/{id}/checks`     | проверить монитор сейчас (201)                  |
| `GET /monitors/{id}/checks`      | последние проверки, `?limit=1..100` (по умолчанию 20) |

Данные (в том числе пользователи) живут в памяти процесса и пропадают при перезапуске; токены при этом перестают подходить, так как пользователей уже нет. Все ошибки имеют вид `{"code": ..., "message": ...}`, а у каждого ответа есть заголовок `X-Request-ID`.

### Настройки API

Читаются при создании приложения; неверное значение роняет запуск, а не первый запрос. Хост и порт задаёт запускающая команда: `uv run fastapi dev --host 0.0.0.0 --port 9000`.

| Переменная                        | По умолчанию | Смысл                                                   |
| --------------------------------- | ------------ | ------------------------------------------------------- |
| `PULSE_API_ENVIRONMENT`           | `dev`        | `dev` или `prod`; в `prod` отключены `/docs` и `/redoc` |
| `PULSE_API_LOG_LEVEL`             | `INFO`       | уровень логов `pulse.*`: DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `PULSE_API_MAX_CHECKS_PER_MONITOR` | `100`       | сколько последних проверок хранить на монитор (>= 1)    |
| `PULSE_API_JWT_SECRET`            | обязательно  | ключ подписи JWT (HS256), не короче 32 символов         |
| `PULSE_API_ACCESS_TOKEN_TTL_MINUTES` | `15`      | срок жизни access-токена, минуты (>= 1)                 |

В `prod` схема `/openapi.json` остаётся доступной (по ней строится клиент фронтенда).
