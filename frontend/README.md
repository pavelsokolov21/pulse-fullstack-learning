# Pulse: frontend

Next.js (App Router) поверх API из `../backend`. Типы и клиент строятся из OpenAPI-схемы бэкенда: `openapi-typescript` и `openapi-fetch`.

## Запуск

Нужны Node.js 20+ и запущенный бэкенд (см. `../backend/README.md`). Бэкенд запускают с путём cookie за прокси:

```bash
# backend/
PULSE_API_JWT_SECRET=$(openssl rand -hex 32) PULSE_API_REFRESH_COOKIE_PATH=/api/auth \
  PULSE_API_DATABASE_URL='postgresql+psycopg://pulse_app:ПАРОЛЬ@127.0.0.1:5432/pulse_schema' \
  PULSE_API_REDIS_URL='redis://:ПАРОЛЬ_REDIS@127.0.0.1:6379/0' \
  uv run fastapi dev --no-proxy-headers

# frontend/
npm install
npm run dev          # http://localhost:3000
```

Браузер обращается только к Next.js: `rewrites` в `next.config.ts` переправляет `/api/*` на FastAPI (по умолчанию `http://localhost:8000`, меняется переменной `API_URL`, см. `.env.example`). Origin один, поэтому CORS не нужен, а refresh-cookie с `SameSite=Strict` не мешает. `API_URL` читается при сборке и старте сервера Next.js.

Страница `/monitors`: таблица мониторов (URL, email владельца, интервал, таймаут) с постраничной навигацией по 10 записей и данные текущего пользователя (email и роль) в шапке. Админ видит все мониторы, остальные только свои. Номер страницы хранится в состоянии компонента, а не в адресе: после перезагрузки откроется первая.

Страница `/tasks` («Фоновые задачи»): выбираете монитор, запускаете проверку и видите, что происходит с задачей «уведомить о сбое» в Celery: в очереди → попытка → повтор через N с (с обратным отсчётом) → отправлено или провал. Данные берутся из `GET /tasks/events` раз в секунду. Нужны воркер и `PULSE_API_TASKS_BROKER_URL` (см. раздел про Celery в `../backend/README.md`); без воркера задачи будут «в очереди». Видно только менеджеру и администратору. Успешная проверка уведомления не создаёт: для демонстрации нужен монитор, который отвечает ошибкой.

Пользователя пока можно создать только через API (`/docs` бэкенда или `curl -X POST localhost:8000/auth/register -H 'Content-Type: application/json' -d '{"email":"a@b.co","password":"correct horse battery"}'`).

## Команды

| Команда                | Что делает                                                       |
| ---------------------- | ---------------------------------------------------------------- |
| `npm run gen:api`      | пишет `openapi.json` из приложения (без запуска сервера) и перегенерирует `src/api/schema.d.ts` |
| `npm run typecheck`    | `next typegen` и `tsc --noEmit`                                   |
| `npm run lint`         | ESLint                                                            |
| `npm test`             | Vitest                                                            |
| `npm run build`        | сборка                                                            |

`openapi.json` и `src/api/schema.d.ts` лежат в git: изменения API видны в диффе. Руками `schema.d.ts` не правим.

## Как устроена сессия

- **Вход.** `POST /auth/login` принимает форму `application/x-www-form-urlencoded` (OAuth2 password flow), а не JSON. Тело собирает `toFormBody` (`src/lib/form.ts`), поле называется `username`, в нём email.
- **Access-токен** живёт в памяти модуля (`src/lib/session-store.ts`), не в `localStorage`: XSS его не прочитает. Живёт 15 минут.
- **Refresh-токен** лежит в httpOnly-cookie, JavaScript его не видит. Запросы идут на тот же origin (`/api/...`), браузер прикладывает cookie сам, а её путь `/api/auth` задаёт бэкенд (`PULSE_API_REFRESH_COOKIE_PATH`).
- **Перезагрузка страницы** стирает память, и защищённые страницы сначала вызывают `/auth/refresh` (`auth.restore()`), чтобы получить новый access по cookie.
- **Истёкший access.** Middleware клиента (`src/api/auth-middleware.ts`) на 401 с кодом `token_expired` один раз вызывает `/auth/refresh` и повторяет запрос. Параллельные запросы делят один refresh: токен одноразовый, и второй вызов сервер счёл бы кражей. Другие 401 не обновляются.
- **Защита страниц** на клиенте: токена в памяти сервер Next.js не видит (cookie с refresh уходит только на `/auth`), поэтому `middleware`/`proxy` для проверки входа не годится.

```
src/
  api/        клиент, auth-middleware, сгенерированные типы
  lib/        хранилище сессии, auth (вход/выход/refresh), форма, хук useSession
  app/        страницы: /login, /monitors (с / на /monitors идёт редирект)
```
