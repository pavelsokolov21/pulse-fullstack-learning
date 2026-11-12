-- Колонка NOT NULL без долгой блокировки: CHECK ... NOT VALID -> VALIDATE -> SET NOT NULL.
\set ON_ERROR_STOP on
SET ROLE pulse_migrator;
\timing on

\echo '--- 1. ADD COLUMN ... DEFAULT (как и раньше, мгновенно)'
ALTER TABLE checks ADD COLUMN region text DEFAULT 'eu';

\echo '--- 2. CHECK ... NOT VALID: строки не проверяются, лок короткий'
BEGIN;
ALTER TABLE checks ADD CONSTRAINT checks_region_not_null
    CHECK (region IS NOT NULL) NOT VALID;
SELECT mode, granted FROM pg_locks
 WHERE relation = 'checks'::regclass AND pid = pg_backend_pid() ORDER BY mode;
COMMIT;

\echo '--- 3. VALIDATE CONSTRAINT: полный скан, но под SHARE UPDATE EXCLUSIVE'
BEGIN;
ALTER TABLE checks VALIDATE CONSTRAINT checks_region_not_null;
SELECT mode, granted FROM pg_locks
 WHERE relation = 'checks'::regclass AND pid = pg_backend_pid() ORDER BY mode;
COMMIT;

\echo '--- 4. SET NOT NULL: PG 12+ видит проверенный CHECK и пропускает скан'
SET client_min_messages = debug1;
BEGIN;
ALTER TABLE checks ALTER COLUMN region SET NOT NULL;
SELECT mode, granted FROM pg_locks
 WHERE relation = 'checks'::regclass AND pid = pg_backend_pid() ORDER BY mode;
COMMIT;
SET client_min_messages = notice;

\echo '--- 5. временный CHECK больше не нужен'
ALTER TABLE checks DROP CONSTRAINT checks_region_not_null;
