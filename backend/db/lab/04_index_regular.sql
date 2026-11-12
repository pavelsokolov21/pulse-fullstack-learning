-- Обычный CREATE INDEX: берёт SHARE на таблицу, пока строится индекс. Чтение идёт,
-- а INSERT/UPDATE/DELETE стоят в очереди.
SET ROLE pulse_migrator;
\timing on
CREATE INDEX checks_checked_at_idx ON checks (checked_at);
