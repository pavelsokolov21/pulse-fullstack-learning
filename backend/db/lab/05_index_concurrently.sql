-- CREATE INDEX CONCURRENTLY: два прохода по таблице, берёт SHARE UPDATE EXCLUSIVE,
-- записи не мешает. Нельзя внутри транзакции (в Alembic: autocommit_block).
SET ROLE pulse_migrator;
\timing on
CREATE INDEX CONCURRENTLY checks_checked_at_idx ON checks (checked_at);
SELECT indexrelid::regclass AS idx, indisvalid, pg_size_pretty(pg_relation_size(indexrelid)) AS size
  FROM pg_index WHERE indexrelid = 'checks_checked_at_idx'::regclass;
