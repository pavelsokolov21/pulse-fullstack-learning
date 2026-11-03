-- Роли Pulse. Запускать СУПЕРПОЛЬЗОВАТЕЛЕМ до schema.sql:
--   psql -v ON_ERROR_STOP=1 -v migrator_password=... -v app_password=... -f db/roles.sql
--
-- Две роли с разными обязанностями:
--   pulse_migrator  владелец схемы: создаёт и меняет таблицы (миграции, Alembic).
--   pulse_app       роль приложения: только данные, прав на DDL нет, ничем не владеет.
--
-- Приложение никогда не подключается владельцем: владелец может всё со своими
-- таблицами, в том числе стереть журнал аудита. Пароли передаются переменными
-- psql и в репозиторий не попадают. Роли общие на весь кластер, а не на базу.
-- Скрипт можно запускать повторно: роли создаются один раз, пароли обновляются.

DO $do$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pulse_migrator') THEN
        CREATE ROLE pulse_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pulse_app') THEN
        CREATE ROLE pulse_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$do$;

ALTER ROLE pulse_migrator PASSWORD :'migrator_password';
ALTER ROLE pulse_app PASSWORD :'app_password';
