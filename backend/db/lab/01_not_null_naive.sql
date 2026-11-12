-- Колонка NOT NULL «в лоб»: DEFAULT, затем SET NOT NULL.
\set ON_ERROR_STOP on
SET ROLE pulse_migrator;
\timing on

\echo '--- 1. ADD COLUMN ... DEFAULT: константа, таблица не переписывается (PG 11+)'
BEGIN;
ALTER TABLE checks ADD COLUMN region text DEFAULT 'eu';
SELECT mode, granted FROM pg_locks
 WHERE relation = 'checks'::regclass AND pid = pg_backend_pid() ORDER BY mode;
COMMIT;

\echo '--- 2. SET NOT NULL: проверка каждой строки под ACCESS EXCLUSIVE'
SET client_min_messages = debug1; -- сервер сам напишет «verifying table»
BEGIN;
ALTER TABLE checks ALTER COLUMN region SET NOT NULL;
SELECT mode, granted FROM pg_locks
 WHERE relation = 'checks'::regclass AND pid = pg_backend_pid() ORDER BY mode;
COMMIT;
