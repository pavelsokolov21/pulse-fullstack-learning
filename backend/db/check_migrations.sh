#!/usr/bin/env bash
# Локальная проверка миграций (позже переедет в CI):
#   1. пустая база;
#   2. alembic upgrade head   - все миграции накатываются с нуля;
#   3. alembic check          - схема в базе совпадает с ORM-моделями;
#   4. downgrade base + снова upgrade head - каждая миграция откатывается и накатывается.
# Потом временная база удаляется, даже если проверка упала.
#
#   uv run poe check-migrations      (или db/check_migrations.sh)
#
# Нужен PULSE_MIGRATE_DATABASE_URL (роль pulse_migrator; имя базы в адресе любое, оно
# заменяется на временное). Если переменной нет, читается backend/.env.migrate.
# Базу создаёт суперпользователь через `docker compose exec db` (по локальному сокету,
# без пароля), владельцем становится pulse_migrator, как в бою. Рабочие базы не
# трогаются. В CI вместо docker compose будет сервис Postgres с тем же psql.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${PULSE_MIGRATE_DATABASE_URL:-}" && -f .env.migrate ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.migrate
  set +a
fi
: "${PULSE_MIGRATE_DATABASE_URL:?Задайте PULSE_MIGRATE_DATABASE_URL (или backend/.env.migrate)}"

db="pulse_ci_$$"
# Адрес без имени базы + временное имя. Параметры после «?» не поддерживаются.
export PULSE_MIGRATE_DATABASE_URL="${PULSE_MIGRATE_DATABASE_URL%/*}/${db}"

admin_psql() {
  (cd .. && docker compose exec -T db sh -c \
    "psql -U \"\$POSTGRES_USER\" -d postgres -X -v ON_ERROR_STOP=1 -q -c \"$1\"")
}

cleanup() { admin_psql "DROP DATABASE IF EXISTS ${db} WITH (FORCE)" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== создаю пустую базу ${db}"
admin_psql "CREATE DATABASE ${db} OWNER pulse_migrator"

echo "== alembic upgrade head"
uv run alembic upgrade head

echo "== alembic check (схема совпадает с моделями)"
uv run alembic check

echo "== downgrade base, затем upgrade head"
uv run alembic downgrade base
uv run alembic upgrade head

echo "OK: миграции накатываются с нуля, откатываются и совпадают с моделями"
