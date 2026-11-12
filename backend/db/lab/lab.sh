#!/usr/bin/env bash
# Лаборатория недели 7: копия базы с 1M+ проверок и запуск SQL-сценариев на ней.
#
#   db/lab/lab.sh reset          пересоздать pulse_lab как копию pulse_schema
#   db/lab/lab.sh psql FILE.sql  выполнить сценарий в pulse_lab
#   db/lab/lab.sh shell          psql в pulse_lab
#
# Ходит в контейнер db через docker compose: внутри контейнера суперпользователь
# входит по локальному сокету без пароля, поэтому секретов в скрипте нет.
# Рабочую базу pulse_schema скрипт только читает (как шаблон) и никогда не меняет.
set -euo pipefail
CALLER_DIR="$PWD"
cd "$(dirname "$0")/../../.."

in_db() {
  docker compose exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -X -v ON_ERROR_STOP=1 $*"
}

case "${1:-}" in
  reset)
    # CREATE DATABASE ... TEMPLATE копирует файлы базы целиком (секунды на 200 МБ).
    # Условие: к шаблону никто не подключён, поэтому API и нагрузчик на pulse_schema
    # на время копирования надо остановить.
    in_db "-d postgres \
      -c 'DROP DATABASE IF EXISTS pulse_lab WITH (FORCE)' \
      -c 'CREATE DATABASE pulse_lab TEMPLATE pulse_schema OWNER pulse_migrator'"
    ;;
  psql)
    file="${2:?укажите файл сценария}"
    [[ "$file" = /* ]] || file="$CALLER_DIR/$file"
    in_db "-d pulse_lab" < "$file"
    ;;
  shell)
    docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d pulse_lab'
    ;;
  *)
    sed -n '2,10p' "$0"
    exit 1
    ;;
esac
