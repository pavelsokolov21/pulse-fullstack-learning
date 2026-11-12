-- Запросы по checked_at без привязки к монитору. Нужны до и после индекса.
-- (a) «за последний час» как в приложении; (b) час посреди данных (~1.3k строк);
-- (c) сутки (~32k строк, 3% таблицы).
SET ROLE pulse_migrator;
SET max_parallel_workers_per_gather = 0; -- чтобы цифры не зависели от числа ядер

\echo '=== (a) последний час: checked_at >= now() - 1 hour'
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT id, monitor_id, status_code, latency_ms, checked_at FROM checks
 WHERE checked_at >= '2026-11-11 16:30+00';

\echo '=== (b) один час посреди данных'
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT id, monitor_id, status_code, latency_ms, checked_at FROM checks
 WHERE checked_at >= '2026-11-04 12:00+00' AND checked_at < '2026-11-04 13:00+00';

\echo '=== (c) сутки, агрегат'
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT count(*), count(*) FILTER (WHERE ok) FROM checks
 WHERE checked_at >= '2026-11-04 00:00+00' AND checked_at < '2026-11-05 00:00+00';
