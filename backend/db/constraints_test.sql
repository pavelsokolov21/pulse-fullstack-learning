-- Проверка ограничений схемы: каждое правило из schema.sql должно сработать.
--
-- Запуск на пустой базе, куда уже применены roles.sql и schema.sql (см. README).
-- Запускать суперпользователем: часть проверок идёт от имени pulse_app
-- через SET ROLE.
--   psql -v ON_ERROR_STOP=1 -f db/constraints_test.sql
--
-- Всё идёт в одной транзакции и откатывается в конце: база остаётся пустой.
-- Любое расхождение прерывает скрипт ошибкой FAIL [...] и ненулевым кодом выхода.
-- Если БД без расширения btree_gist (нет EXCLUDE), добавьте -v skip_exclusion=1.

\set ON_ERROR_STOP on

BEGIN;

-- Результаты вызовов проверок не нужны на экране, важны только ошибки и итог.
\o /dev/null

CREATE TEMP TABLE passed (label text);

-- Оператор должен упасть на ограничении с именем (или столбцом) `expected`.
CREATE FUNCTION pg_temp.violates(label text, stmt text, expected text) RETURNS void
LANGUAGE plpgsql AS $fn$
DECLARE
    got_constraint text;
    got_column text;
BEGIN
    BEGIN
        EXECUTE stmt;
    EXCEPTION WHEN integrity_constraint_violation THEN
        GET STACKED DIAGNOSTICS got_constraint = CONSTRAINT_NAME, got_column = COLUMN_NAME;
        -- Для NOT NULL до PG 18 имени ограничения нет, тогда сверяем столбец.
        IF expected IS DISTINCT FROM got_constraint AND expected IS DISTINCT FROM got_column THEN
            RAISE EXCEPTION 'FAIL [%]: сработало "%" (столбец "%"), ожидалось "%"',
                label, got_constraint, got_column, expected;
        END IF;
        INSERT INTO passed VALUES (label);
        RETURN;
    END;
    RAISE EXCEPTION 'FAIL [%]: оператор выполнился, а должен был упасть на "%"', label, expected;
END
$fn$;

-- Оператор должен выполниться без ошибок.
CREATE FUNCTION pg_temp.works(label text, stmt text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
    EXECUTE stmt;
    INSERT INTO passed VALUES (label);
END
$fn$;

-- Оператор от имени приложения (pulse_app) должен выполниться / получить отказ в правах.
-- SET LOCAL ROLE откатывается вместе с подтранзакцией при ошибке, при успехе
-- роль сбрасывается явно.
CREATE FUNCTION pg_temp.app_works(label text, stmt text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
    SET LOCAL ROLE pulse_app;
    EXECUTE stmt;
    RESET ROLE;
    INSERT INTO passed VALUES (label);
END
$fn$;

CREATE FUNCTION pg_temp.app_denied(label text, stmt text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
    BEGIN
        SET LOCAL ROLE pulse_app;
        EXECUTE stmt;
        RESET ROLE;
    EXCEPTION WHEN insufficient_privilege THEN
        INSERT INTO passed VALUES (label);
        RETURN;
    END;
    RAISE EXCEPTION 'FAIL [%]: приложению это разрешено, а не должно быть', label;
END
$fn$;

-- Запрос возвращает одно значение, и оно равно ожидаемому (сравнение как текст).
CREATE FUNCTION pg_temp.expect(label text, query text, expected text) RETURNS void
LANGUAGE plpgsql AS $fn$
DECLARE
    got text;
BEGIN
    EXECUTE query INTO got;
    IF got IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'FAIL [%]: получено "%", ожидалось "%"', label, got, expected;
    END IF;
    INSERT INTO passed VALUES (label);
END
$fn$;

-- Короткие поиски идентификаторов по именам из тестовых данных.
CREATE FUNCTION pg_temp.u(name text) RETURNS bigint LANGUAGE sql AS
    $$ SELECT id FROM users WHERE email = name || '@x.io' $$;
CREATE FUNCTION pg_temp.m(url text) RETURNS bigint LANGUAGE sql AS
    $$ SELECT id FROM monitors WHERE monitors.url = m.url $$;
CREATE FUNCTION pg_temp.c(name text) RETURNS bigint LANGUAGE sql AS
    $$ SELECT id FROM channels WHERE channels.name = c.name $$;

-- Хеш токена нужной формы: 64 hex-символа.
CREATE FUNCTION pg_temp.h(n int) RETURNS text LANGUAGE sql AS
    $$ SELECT lpad(to_hex(n), 64, '0') $$;


-- ===========================================================================
-- Исходные данные: корректные строки должны проходить
-- ===========================================================================
SELECT pg_temp.works('users: корректные записи', $$
    INSERT INTO users (email, password_hash, role) VALUES
        ('alice@x.io', 'hash-a', 'manager'), ('bob@x.io', 'hash-b', 'manager'),
        ('carol@x.io', 'hash-c', 'manager')
$$);
SELECT pg_temp.works('users: все три роли допустимы', $$
    INSERT INTO users (email, password_hash, role) VALUES
        ('adm@x.io', 'h', 'admin'), ('mgr@x.io', 'h', 'manager'), ('vw@x.io', 'h', 'viewer')
$$);

SELECT pg_temp.works('monitors: корректные записи', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES
        (pg_temp.u('alice'), 'https://a.io', 300),
        (pg_temp.u('alice'), 'http://a2.io/health?x=1', 30),
        (pg_temp.u('bob'),   'https://b.io', 3600)
$$);
SELECT pg_temp.expect('monitors: таймаут по умолчанию 10 секунд',
    $$ SELECT timeout_seconds FROM monitors WHERE url = 'https://a.io' $$, '10');

SELECT pg_temp.works('channels: корректные записи', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES
        (pg_temp.u('alice'), 'ops-mail', 'email',   'ops@x.io'),
        (pg_temp.u('alice'), 'hook',     'webhook', 'https://hooks.x.io/1'),
        (pg_temp.u('alice'), 'chat',     'slack',   'https://hooks.slack.com/x'),
        (pg_temp.u('bob'),   'bob-mail', 'email',   'bob@x.io'),
        (pg_temp.u('carol'), 'carol-mail', 'email', 'carol@x.io')
$$);

SELECT pg_temp.works('monitor_channels: канал владельца к монитору владельца', $$
    INSERT INTO monitor_channels (monitor_id, channel_id, owner_id) VALUES
        (pg_temp.m('https://a.io'), pg_temp.c('ops-mail'), pg_temp.u('alice')),
        (pg_temp.m('https://a.io'), pg_temp.c('hook'),     pg_temp.u('alice')),
        (pg_temp.m('https://b.io'), pg_temp.c('bob-mail'), pg_temp.u('bob'))
$$);

SELECT pg_temp.works('checks: успешная и неудачная проверки', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, error, checked_at) VALUES
        (pg_temp.m('https://a.io'), 'https://a.io', 200, 12.5, NULL, '2026-01-01 10:00+00'),
        (pg_temp.m('https://a.io'), 'https://a.io', NULL, 3000, 'Network Error: ConnectError', '2026-01-01 10:05+00')
$$);

SELECT pg_temp.works('refresh_tokens: корректная запись', $$
    INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)
    VALUES (pg_temp.u('alice'), gen_random_uuid(), pg_temp.h(1), '2026-02-01 00:00+00')
$$);

SELECT pg_temp.works('audit_log: корректная запись', $$
    INSERT INTO audit_log (actor_id, action, entity_type, entity_id, details, request_id, ip)
    VALUES (pg_temp.u('alice'), 'monitor.create', 'monitor', pg_temp.m('https://a.io'),
            '{"url": "https://a.io"}', 'req-1', '203.0.113.7')
$$);


-- ===========================================================================
-- users
-- ===========================================================================
SELECT pg_temp.violates('users: email уникален',
    $$ INSERT INTO users (email, password_hash, role) VALUES ('alice@x.io', 'h', 'manager') $$, 'users_email_key');
SELECT pg_temp.violates('users: email в верхнем регистре отвергается',
    $$ INSERT INTO users (email, password_hash, role) VALUES ('Dave@x.io', 'h', 'manager') $$, 'users_email_normalized');
SELECT pg_temp.violates('users: пробел по краям email отвергается',
    $$ INSERT INTO users (email, password_hash, role) VALUES (' dave@x.io', 'h', 'manager') $$, 'users_email_format');
SELECT pg_temp.violates('users: email без @',
    $$ INSERT INTO users (email, password_hash, role) VALUES ('dave.x.io', 'h', 'manager') $$, 'users_email_format');
SELECT pg_temp.violates('users: пробел внутри email',
    $$ INSERT INTO users (email, password_hash, role) VALUES ('da ve@x.io', 'h', 'manager') $$, 'users_email_format');
SELECT pg_temp.violates('users: два @ в email',
    $$ INSERT INTO users (email, password_hash, role) VALUES ('a@b@x.io', 'h', 'manager') $$, 'users_email_format');
SELECT pg_temp.violates('users: email длиннее 254 символов',
    $$ INSERT INTO users (email, password_hash, role) VALUES (repeat('a', 250) || '@x.io', 'h', 'manager') $$, 'users_email_format');
SELECT pg_temp.violates('users: email обязателен',
    $$ INSERT INTO users (email, password_hash, role) VALUES (NULL, 'h', 'manager') $$, 'email');
SELECT pg_temp.violates('users: хеш пароля обязателен',
    $$ INSERT INTO users (email, password_hash, role) VALUES ('dave@x.io', NULL, 'manager') $$, 'password_hash');
SELECT pg_temp.violates('users: пустой хеш пароля',
    $$ INSERT INTO users (email, password_hash, role) VALUES ('dave@x.io', '   ', 'manager') $$, 'users_password_hash_not_blank');
SELECT pg_temp.violates('users: неизвестная роль',
    $$ INSERT INTO users (email, password_hash, role) VALUES ('dave@x.io', 'h', 'root') $$, 'users_role_valid');
SELECT pg_temp.violates('users: роль обязательна',
    $$ INSERT INTO users (email, password_hash, role) VALUES ('dave@x.io', 'h', NULL) $$, 'role');
SELECT pg_temp.violates('users: у роли нет значения по умолчанию',
    $$ INSERT INTO users (email, password_hash) VALUES ('dave@x.io', 'h') $$, 'role');


-- ===========================================================================
-- refresh_tokens
-- ===========================================================================
SELECT pg_temp.violates('refresh_tokens: хеш токена уникален', $$
    INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)
    VALUES (pg_temp.u('alice'), gen_random_uuid(), pg_temp.h(1), '2026-02-01 00:00+00')
$$, 'refresh_tokens_token_hash_key');
SELECT pg_temp.violates('refresh_tokens: хеш не sha256 (короткий)', $$
    INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)
    VALUES (pg_temp.u('alice'), gen_random_uuid(), 'abc', '2026-02-01 00:00+00')
$$, 'refresh_tokens_token_hash_sha256');
SELECT pg_temp.violates('refresh_tokens: хеш в верхнем регистре отвергается', $$
    INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)
    VALUES (pg_temp.u('alice'), gen_random_uuid(), upper(pg_temp.h(255)), '2026-02-01 00:00+00')
$$, 'refresh_tokens_token_hash_sha256');
SELECT pg_temp.violates('refresh_tokens: пользователь должен существовать', $$
    INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)
    VALUES (-1, gen_random_uuid(), pg_temp.h(2), '2026-02-01 00:00+00')
$$, 'refresh_tokens_user_id_fkey');
SELECT pg_temp.violates('refresh_tokens: family_id обязателен', $$
    INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)
    VALUES (pg_temp.u('alice'), NULL, pg_temp.h(3), '2026-02-01 00:00+00')
$$, 'family_id');
SELECT pg_temp.violates('refresh_tokens: срок обязателен', $$
    INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)
    VALUES (pg_temp.u('alice'), gen_random_uuid(), pg_temp.h(4), NULL)
$$, 'expires_at');
SELECT pg_temp.expect('refresh_tokens: новый токен не отозван',
    $$ SELECT revoked_at IS NULL FROM refresh_tokens WHERE token_hash = pg_temp.h(1) $$, 'true');


-- ===========================================================================
-- monitors
-- ===========================================================================
SELECT pg_temp.violates('monitors: URL уникален у владельца', $$
    INSERT INTO monitors (owner_id, url, interval_seconds)
    VALUES (pg_temp.u('alice'), 'https://a.io', 60)
$$, 'monitors_owner_url_key');
SELECT pg_temp.works('monitors: тот же URL у другого владельца разрешён', $$
    INSERT INTO monitors (owner_id, url, interval_seconds)
    VALUES (pg_temp.u('bob'), 'https://a.io', 60)
$$);
SELECT pg_temp.violates('monitors: владелец должен существовать', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (-1, 'https://z.io', 60)
$$, 'monitors_owner_id_fkey');
SELECT pg_temp.violates('monitors: владелец обязателен', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (NULL, 'https://z.io', 60)
$$, 'owner_id');
SELECT pg_temp.violates('monitors: URL без схемы', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (pg_temp.u('alice'), 'z.io', 60)
$$, 'monitors_url_format');
SELECT pg_temp.violates('monitors: схема ftp не допускается', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (pg_temp.u('alice'), 'ftp://z.io', 60)
$$, 'monitors_url_format');
SELECT pg_temp.violates('monitors: пробел в URL', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (pg_temp.u('alice'), 'https://z.io/a b', 60)
$$, 'monitors_url_format');
SELECT pg_temp.violates('monitors: URL длиннее 2048', $$
    INSERT INTO monitors (owner_id, url, interval_seconds)
    VALUES (pg_temp.u('alice'), 'https://z.io/' || repeat('a', 2048), 60)
$$, 'monitors_url_format');
SELECT pg_temp.violates('monitors: интервал короче минимума (29 с)', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (pg_temp.u('alice'), 'https://z.io', 29)
$$, 'monitors_interval_range');
SELECT pg_temp.violates('monitors: интервал длиннее максимума (сутки и секунда)', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (pg_temp.u('alice'), 'https://z.io', 86401)
$$, 'monitors_interval_range');
SELECT pg_temp.violates('monitors: нулевой интервал', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (pg_temp.u('alice'), 'https://z.io', 0)
$$, 'monitors_interval_range');
SELECT pg_temp.violates('monitors: отрицательный интервал', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (pg_temp.u('alice'), 'https://z.io', -300)
$$, 'monitors_interval_range');
SELECT pg_temp.violates('monitors: интервал обязателен', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES (pg_temp.u('alice'), 'https://z.io', NULL)
$$, 'interval_seconds');
SELECT pg_temp.works('monitors: границы интервала включены (30 с и 24 ч)', $$
    INSERT INTO monitors (owner_id, url, interval_seconds) VALUES
        (pg_temp.u('alice'), 'https://min.io', 30), (pg_temp.u('alice'), 'https://max.io', 86400)
$$);
SELECT pg_temp.violates('monitors: нулевой таймаут', $$
    INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)
    VALUES (pg_temp.u('alice'), 'https://z.io', 60, 0)
$$, 'monitors_timeout_positive');
SELECT pg_temp.violates('monitors: отрицательный таймаут', $$
    INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)
    VALUES (pg_temp.u('alice'), 'https://z.io', 60, -1)
$$, 'monitors_timeout_positive');
SELECT pg_temp.violates('monitors: таймаут равен интервалу', $$
    INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)
    VALUES (pg_temp.u('alice'), 'https://z.io', 60, 60)
$$, 'monitors_timeout_below_interval');
SELECT pg_temp.violates('monitors: таймаут длиннее интервала', $$
    INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)
    VALUES (pg_temp.u('alice'), 'https://z.io', 30, 45)
$$, 'monitors_timeout_below_interval');
SELECT pg_temp.violates('monitors: таймаут NaN', $$
    INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)
    VALUES (pg_temp.u('alice'), 'https://z.io', 60, 'NaN')
$$, 'monitors_timeout_below_interval');
SELECT pg_temp.violates('monitors: таймаут Infinity', $$
    INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)
    VALUES (pg_temp.u('alice'), 'https://z.io', 60, 'Infinity')
$$, 'monitors_timeout_below_interval');
SELECT pg_temp.works('monitors: таймаут чуть меньше интервала допустим', $$
    INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)
    VALUES (pg_temp.u('alice'), 'https://tight.io', 30, 29.999)
$$);
SELECT pg_temp.violates('monitors: UPDATE таймаута до значения интервала', $$
    UPDATE monitors SET timeout_seconds = 30 WHERE url = 'https://tight.io'
$$, 'monitors_timeout_below_interval');
SELECT pg_temp.works('monitors: монитор с длинным таймаутом', $$
    INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)
    VALUES (pg_temp.u('alice'), 'https://wide.io', 300, 100)
$$);
SELECT pg_temp.violates('monitors: UPDATE сокращает интервал ниже таймаута', $$
    UPDATE monitors SET interval_seconds = 60 WHERE url = 'https://wide.io'
$$, 'monitors_timeout_below_interval');
SELECT pg_temp.expect('monitors: таймаут по умолчанию 10 секунд',
    $$ SELECT timeout_seconds FROM monitors WHERE url = 'https://min.io' $$, '10');
SELECT pg_temp.works('monitors: дробный таймаут допустим', $$
    INSERT INTO monitors (owner_id, url, interval_seconds, timeout_seconds)
    VALUES (pg_temp.u('alice'), 'https://frac.io', 60, 0.5)
$$);
SELECT pg_temp.violates('monitors: пользователя с мониторами удалить нельзя (RESTRICT)',
    $$ DELETE FROM users WHERE email = 'alice@x.io' $$, 'monitors_owner_id_fkey');


-- ===========================================================================
-- checks
-- ===========================================================================
SELECT pg_temp.violates('checks: монитор должен существовать', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
    VALUES (-1, 'https://a.io', 200, 1, now())
$$, 'checks_monitor_id_fkey');
SELECT pg_temp.violates('checks: URL с неверной схемой', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'a.io', 200, 1, now())
$$, 'checks_url_format');
SELECT pg_temp.violates('checks: код ответа меньше 100', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', 99, 1, now())
$$, 'checks_status_code_range');
SELECT pg_temp.violates('checks: код ответа больше 599', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', 600, 1, now())
$$, 'checks_status_code_range');
SELECT pg_temp.violates('checks: отрицательная задержка', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', 200, -1, now())
$$, 'checks_latency_finite_non_negative');
SELECT pg_temp.violates('checks: задержка NaN', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', 200, 'NaN', now())
$$, 'checks_latency_finite_non_negative');
SELECT pg_temp.violates('checks: задержка Infinity', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', 200, 'Infinity', now())
$$, 'checks_latency_finite_non_negative');
SELECT pg_temp.violates('checks: есть и код, и ошибка', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, error, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', 200, 1, 'boom', now())
$$, 'checks_status_xor_error');
SELECT pg_temp.violates('checks: нет ни кода, ни ошибки', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, error, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', NULL, 1, NULL, now())
$$, 'checks_status_xor_error');
SELECT pg_temp.violates('checks: пустой текст ошибки', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, error, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', NULL, 1, '  ', now())
$$, 'checks_error_not_blank');
SELECT pg_temp.violates('checks: время проверки обязательно', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', 200, 1, NULL)
$$, 'checked_at');
SELECT pg_temp.violates('checks: задержка обязательна', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
    VALUES (pg_temp.m('https://a.io'), 'https://a.io', 200, NULL, now())
$$, 'latency_ms');

-- ok выводится из кода ответа и не может разойтись с ним.
SELECT pg_temp.works('checks: коды для проверки столбца ok', $$
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, error, checked_at)
    SELECT pg_temp.m('https://b.io'), 'https://b.io', code, 1,
           CASE WHEN code IS NULL THEN 'timeout' END, now()
    FROM unnest(ARRAY[199, 200, 399, 400, 503, NULL]::int[]) AS code
$$);
SELECT pg_temp.expect('checks: ok истинно только для 200..399',
    $$ SELECT string_agg(coalesce(status_code::text, 'none') || '=' || ok, ' ' ORDER BY status_code NULLS LAST)
       FROM checks WHERE monitor_id = pg_temp.m('https://b.io') $$,
    '199=false 200=true 399=true 400=false 503=false none=false');


-- ===========================================================================
-- incidents
-- ===========================================================================
SELECT pg_temp.works('incidents: закрытый инцидент', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at, failed_checks, last_error)
    VALUES (pg_temp.m('https://a.io'), '2026-01-01 10:00+00', '2026-01-01 10:30+00', 3, 'timeout')
$$);
SELECT pg_temp.works('incidents: следующий инцидент начинается там, где закончился предыдущий', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at)
    VALUES (pg_temp.m('https://a.io'), '2026-01-01 10:30+00', '2026-01-01 11:00+00')
$$);
SELECT pg_temp.works('incidents: открытый инцидент после закрытых', $$
    INSERT INTO incidents (monitor_id, started_at) VALUES (pg_temp.m('https://a.io'), '2026-01-01 12:00+00')
$$);
SELECT pg_temp.works('incidents: у другого монитора то же время разрешено', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at)
    VALUES (pg_temp.m('https://b.io'), '2026-01-01 10:00+00', '2026-01-01 10:30+00')
$$);
SELECT pg_temp.violates('incidents: монитор должен существовать', $$
    INSERT INTO incidents (monitor_id, started_at) VALUES (-1, now())
$$, 'incidents_monitor_id_fkey');
SELECT pg_temp.violates('incidents: монитор обязателен', $$
    INSERT INTO incidents (monitor_id, started_at) VALUES (NULL, now())
$$, 'monitor_id');
SELECT pg_temp.violates('incidents: начало обязательно', $$
    INSERT INTO incidents (monitor_id, started_at) VALUES (pg_temp.m('https://b.io'), NULL)
$$, 'started_at');
SELECT pg_temp.violates('incidents: ноль неудачных проверок', $$
    INSERT INTO incidents (monitor_id, started_at, failed_checks)
    VALUES (pg_temp.m('https://b.io'), '2026-02-01 00:00+00', 0)
$$, 'incidents_failed_checks_positive');
SELECT pg_temp.violates('incidents: закрытие раньше начала', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at)
    VALUES (pg_temp.m('https://b.io'), '2026-02-01 10:00+00', '2026-02-01 09:00+00')
$$, 'incidents_resolved_after_started');
SELECT pg_temp.violates('incidents: нулевая длительность', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at)
    VALUES (pg_temp.m('https://b.io'), '2026-02-01 10:00+00', '2026-02-01 10:00+00')
$$, 'incidents_resolved_after_started');
SELECT pg_temp.violates('incidents: пустой текст ошибки', $$
    INSERT INTO incidents (monitor_id, started_at, last_error)
    VALUES (pg_temp.m('https://b.io'), '2026-02-01 00:00+00', ' ')
$$, 'incidents_last_error_not_blank');
SELECT pg_temp.violates('incidents: проверка-открыватель должна существовать', $$
    INSERT INTO incidents (monitor_id, started_at, opened_by_check_id)
    VALUES (pg_temp.m('https://b.io'), '2026-02-01 00:00+00', -1)
$$, 'incidents_opened_by_check_fkey');
SELECT pg_temp.violates('incidents: проверка-закрыватель должна существовать', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at, resolved_by_check_id)
    VALUES (pg_temp.m('https://b.io'), '2026-02-01 00:00+00', '2026-02-01 01:00+00', -1)
$$, 'incidents_resolved_by_check_fkey');

-- Инцидент может ссылаться только на проверки СВОЕГО монитора (составной ключ).
-- Интервалы выбраны так, чтобы не пересекаться с инцидентами монитора: иначе
-- раньше внешнего ключа сработал бы incidents_no_overlap.
SELECT pg_temp.violates('incidents: проверка-открыватель чужого монитора', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at, opened_by_check_id)
    SELECT pg_temp.m('https://a.io'), '2025-12-01 00:00+00', '2025-12-01 01:00+00', id
    FROM checks WHERE monitor_id = pg_temp.m('https://b.io') AND status_code = 400
$$, 'incidents_opened_by_check_fkey');
SELECT pg_temp.violates('incidents: проверка-закрыватель чужого монитора', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at, resolved_by_check_id)
    SELECT pg_temp.m('https://a.io'), '2025-12-01 00:00+00', '2025-12-01 01:00+00', id
    FROM checks WHERE monitor_id = pg_temp.m('https://b.io') AND status_code = 400
$$, 'incidents_resolved_by_check_fkey');
SELECT pg_temp.works('incidents: проверка своего монитора допустима', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at, opened_by_check_id)
    SELECT pg_temp.m('https://a.io'), '2025-12-01 00:00+00', '2025-12-01 01:00+00', id
    FROM checks WHERE monitor_id = pg_temp.m('https://a.io') AND status_code = 200
$$);
DELETE FROM incidents WHERE started_at = '2025-12-01 00:00+00';

-- Удаление старой проверки не должно уносить инцидент (ON DELETE SET NULL).
SELECT pg_temp.works('incidents: инцидент со ссылками на проверки', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at, opened_by_check_id, resolved_by_check_id)
    SELECT pg_temp.m('https://b.io'), '2026-03-01 00:00+00', '2026-03-01 01:00+00',
           (SELECT id FROM checks WHERE monitor_id = pg_temp.m('https://b.io') AND status_code = 503),
           (SELECT id FROM checks WHERE monitor_id = pg_temp.m('https://b.io') AND status_code = 200)
$$);
DELETE FROM checks WHERE monitor_id = pg_temp.m('https://b.io') AND status_code IN (503, 200);
SELECT pg_temp.expect('incidents: после удаления проверок инцидент остался, ссылки обнулились',
    $$ SELECT opened_by_check_id IS NULL AND resolved_by_check_id IS NULL
       FROM incidents WHERE started_at = '2026-03-01 00:00+00' $$, 'true');
-- Главное отличие SET NULL (столбец) от обычного SET NULL: монитор не теряется.
SELECT pg_temp.expect('incidents: monitor_id сохранился после удаления проверок',
    $$ SELECT monitor_id = pg_temp.m('https://b.io')
       FROM incidents WHERE started_at = '2026-03-01 00:00+00' $$, 'true');

-- Пересечение инцидентов монитора запрещает EXCLUDE (нужен btree_gist).
\if :{?skip_exclusion}
\echo 'ПРОПУЩЕНО: incidents_no_overlap (skip_exclusion)'
\else
SELECT pg_temp.violates('incidents: второй открытый инцидент у монитора', $$
    INSERT INTO incidents (monitor_id, started_at) VALUES (pg_temp.m('https://a.io'), '2026-01-01 13:00+00')
$$, 'incidents_no_overlap');
SELECT pg_temp.violates('incidents: пересечение с закрытым инцидентом', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at)
    VALUES (pg_temp.m('https://a.io'), '2026-01-01 10:15+00', '2026-01-01 10:45+00')
$$, 'incidents_no_overlap');
SELECT pg_temp.violates('incidents: вложенный в чужой интервал', $$
    INSERT INTO incidents (monitor_id, started_at, resolved_at)
    VALUES (pg_temp.m('https://a.io'), '2026-01-01 10:10+00', '2026-01-01 10:20+00')
$$, 'incidents_no_overlap');
\endif


-- ===========================================================================
-- channels
-- ===========================================================================
SELECT pg_temp.violates('channels: имя уникально у владельца', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (pg_temp.u('alice'), 'hook', 'email', 'x@x.io')
$$, 'channels_owner_name_key');
SELECT pg_temp.works('channels: то же имя у другого владельца разрешено', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (pg_temp.u('bob'), 'hook', 'email', 'x@x.io')
$$);
SELECT pg_temp.violates('channels: владелец должен существовать', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (-1, 'n', 'email', 'x@x.io')
$$, 'channels_owner_id_fkey');
SELECT pg_temp.violates('channels: пустое имя', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (pg_temp.u('bob'), '  ', 'email', 'x@x.io')
$$, 'channels_name_not_blank');
SELECT pg_temp.violates('channels: имя длиннее 100', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (pg_temp.u('bob'), repeat('n', 101), 'email', 'x@x.io')
$$, 'channels_name_not_blank');
SELECT pg_temp.violates('channels: неизвестный вид', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (pg_temp.u('bob'), 'sms', 'sms', '+10000000')
$$, 'channels_kind_valid');
SELECT pg_temp.violates('channels: email-канал без @', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (pg_temp.u('bob'), 'e', 'email', 'not-an-email')
$$, 'channels_target_matches_kind');
SELECT pg_temp.violates('channels: вебхук по http (не https)', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (pg_temp.u('bob'), 'w', 'webhook', 'http://hooks.x.io/1')
$$, 'channels_target_matches_kind');
SELECT pg_temp.violates('channels: slack-канал с адресом почты', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (pg_temp.u('bob'), 's', 'slack', 'bob@x.io')
$$, 'channels_target_matches_kind');
SELECT pg_temp.violates('channels: цель обязательна', $$
    INSERT INTO channels (owner_id, name, kind, target) VALUES (pg_temp.u('bob'), 't', 'email', NULL)
$$, 'target');


-- ===========================================================================
-- monitor_channels
-- ===========================================================================
SELECT pg_temp.violates('monitor_channels: пара монитор-канал уникальна', $$
    INSERT INTO monitor_channels (monitor_id, channel_id, owner_id)
    VALUES (pg_temp.m('https://a.io'), pg_temp.c('ops-mail'), pg_temp.u('alice'))
$$, 'monitor_channels_pkey');
SELECT pg_temp.violates('monitor_channels: чужой канал к своему монитору (владелец монитора)', $$
    INSERT INTO monitor_channels (monitor_id, channel_id, owner_id)
    VALUES (pg_temp.m('http://a2.io/health?x=1'), pg_temp.c('bob-mail'), pg_temp.u('alice'))
$$, 'monitor_channels_channel_fkey');
SELECT pg_temp.violates('monitor_channels: свой канал к чужому монитору (владелец канала)', $$
    INSERT INTO monitor_channels (monitor_id, channel_id, owner_id)
    VALUES (pg_temp.m('https://b.io'), pg_temp.c('ops-mail'), pg_temp.u('alice'))
$$, 'monitor_channels_monitor_fkey');
SELECT pg_temp.violates('monitor_channels: монитор и канал разных владельцев', $$
    INSERT INTO monitor_channels (monitor_id, channel_id, owner_id)
    VALUES (pg_temp.m('https://b.io'), pg_temp.c('chat'), pg_temp.u('bob'))
$$, 'monitor_channels_channel_fkey');
SELECT pg_temp.violates('monitor_channels: владелец обязателен', $$
    INSERT INTO monitor_channels (monitor_id, channel_id, owner_id)
    VALUES (pg_temp.m('http://a2.io/health?x=1'), pg_temp.c('chat'), NULL)
$$, 'owner_id');
SELECT pg_temp.works('monitor_channels: монитор с подпиской для проверки смены владельца', $$
    INSERT INTO monitors (owner_id, url, interval_seconds)
    VALUES (pg_temp.u('alice'), 'https://own.io', 60);
    INSERT INTO monitor_channels (monitor_id, channel_id, owner_id)
    VALUES (pg_temp.m('https://own.io'), pg_temp.c('ops-mail'), pg_temp.u('alice'));
$$);
SELECT pg_temp.violates('monitor_channels: сменить владельца монитора с подписками нельзя', $$
    UPDATE monitors SET owner_id = pg_temp.u('bob') WHERE url = 'https://own.io'
$$, 'monitor_channels_monitor_fkey');

SELECT pg_temp.works('monitor_channels: подписка для проверки каскадов', $$
    INSERT INTO monitor_channels (monitor_id, channel_id, owner_id)
    VALUES (pg_temp.m('http://a2.io/health?x=1'), pg_temp.c('chat'), pg_temp.u('alice'))
$$);
DELETE FROM channels WHERE name = 'chat';
SELECT pg_temp.expect('monitor_channels: удаление канала убирает подписки',
    $$ SELECT count(*) FROM monitor_channels WHERE monitor_id = pg_temp.m('http://a2.io/health?x=1') $$, '0');
SELECT pg_temp.expect('monitor_channels: канал при удалении подписки остаётся',
    $$ SELECT count(*) FROM channels WHERE name = 'ops-mail' $$, '1');


-- ===========================================================================
-- audit_log
-- ===========================================================================
SELECT pg_temp.violates('audit_log: действие не в формате объект.глагол', $$
    INSERT INTO audit_log (action, entity_type) VALUES ('create', 'monitor')
$$, 'audit_log_action_format');
SELECT pg_temp.violates('audit_log: действие в верхнем регистре', $$
    INSERT INTO audit_log (action, entity_type) VALUES ('Monitor.Create', 'monitor')
$$, 'audit_log_action_format');
SELECT pg_temp.violates('audit_log: действие с пустой частью', $$
    INSERT INTO audit_log (action, entity_type) VALUES ('monitor.', 'monitor')
$$, 'audit_log_action_format');
SELECT pg_temp.violates('audit_log: действие обязательно', $$
    INSERT INTO audit_log (action, entity_type) VALUES (NULL, 'monitor')
$$, 'action');
SELECT pg_temp.violates('audit_log: неизвестный тип объекта', $$
    INSERT INTO audit_log (action, entity_type) VALUES ('monitor.create', 'unicorn')
$$, 'audit_log_entity_type_valid');
SELECT pg_temp.violates('audit_log: details должен быть объектом (массив)', $$
    INSERT INTO audit_log (action, entity_type, details) VALUES ('monitor.create', 'monitor', '[]')
$$, 'audit_log_details_is_object');
SELECT pg_temp.violates('audit_log: details должен быть объектом (строка)', $$
    INSERT INTO audit_log (action, entity_type, details) VALUES ('monitor.create', 'monitor', '"text"')
$$, 'audit_log_details_is_object');
SELECT pg_temp.violates('audit_log: details не может быть NULL', $$
    INSERT INTO audit_log (action, entity_type, details) VALUES ('monitor.create', 'monitor', NULL)
$$, 'details');
SELECT pg_temp.violates('audit_log: пустой request_id', $$
    INSERT INTO audit_log (action, entity_type, request_id) VALUES ('monitor.create', 'monitor', ' ')
$$, 'audit_log_request_id_not_blank');
SELECT pg_temp.violates('audit_log: актор должен существовать', $$
    INSERT INTO audit_log (actor_id, action, entity_type) VALUES (-1, 'monitor.create', 'monitor')
$$, 'audit_log_actor_id_fkey');
SELECT pg_temp.works('audit_log: запись системы без актора и ссылка на несуществующий объект', $$
    INSERT INTO audit_log (action, entity_type, entity_id) VALUES ('user.purge', 'user', 999999)
$$);
SELECT pg_temp.expect('audit_log: время и details заполняются по умолчанию',
    $$ SELECT (occurred_at IS NOT NULL AND details = '{}'::jsonb)
       FROM audit_log WHERE action = 'user.purge' $$, 'true');


-- ===========================================================================
-- Права приложения: роль pulse_app
-- ===========================================================================
SELECT pg_temp.expect('роли: pulse_app без суперправ и без права создавать роли и базы',
    $$ SELECT NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls)
       FROM pg_roles WHERE rolname = 'pulse_app' $$, 'true');
SELECT pg_temp.expect('роли: приложение не владеет ни одной таблицей',
    $$ SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = 'pulse_app' $$, '0');

SELECT pg_temp.app_works('app: пишет в журнал', $$
    INSERT INTO audit_log (action, entity_type) VALUES ('monitor.create', 'monitor')
$$);
SELECT pg_temp.app_works('app: читает журнал', $$ SELECT count(*) FROM audit_log $$);
SELECT pg_temp.app_works('app: создаёт монитор, проверку и инцидент, удаляет проверку', $$
    INSERT INTO monitors (owner_id, url, interval_seconds)
        SELECT id, 'https://app.io', 60 FROM users WHERE email = 'alice@x.io';
    INSERT INTO checks (monitor_id, url, status_code, latency_ms, checked_at)
        SELECT id, url, 200, 5, now() FROM monitors WHERE url = 'https://app.io';
    INSERT INTO incidents (monitor_id, started_at)
        SELECT id, '2027-01-01 00:00+00' FROM monitors WHERE url = 'https://app.io';
    DELETE FROM checks WHERE url = 'https://app.io'
$$);
SELECT pg_temp.app_works('app: правит пользователей и токены', $$
    UPDATE users SET role = 'viewer' WHERE email = 'bob@x.io';
    UPDATE refresh_tokens SET revoked_at = now()
$$);

SELECT pg_temp.app_denied('app: UPDATE журнала', $$ UPDATE audit_log SET action = 'x.y' $$);
SELECT pg_temp.app_denied('app: DELETE из журнала', $$ DELETE FROM audit_log $$);
SELECT pg_temp.app_denied('app: TRUNCATE журнала', $$ TRUNCATE audit_log $$);
SELECT pg_temp.app_denied('app: DROP журнала', $$ DROP TABLE audit_log $$);
SELECT pg_temp.app_denied('app: ALTER журнала', $$ ALTER TABLE audit_log ADD COLUMN x int $$);
SELECT pg_temp.app_denied('app: UPDATE результатов проверок', $$ UPDATE checks SET latency_ms = 0 $$);
SELECT pg_temp.app_denied('app: TRUNCATE пользователей', $$ TRUNCATE users CASCADE $$);
SELECT pg_temp.app_denied('app: создание таблиц', $$ CREATE TABLE hack (id int) $$);
SELECT pg_temp.app_denied('app: отключение ограничений', $$ ALTER TABLE monitors DROP CONSTRAINT monitors_timeout_below_interval $$);

-- Удаляя пользователя, приложение обнуляет actor_id в журнале, хотя UPDATE на
-- журнал ему запрещён: ссылочные действия идут с правами владельца таблицы.
SELECT pg_temp.works('роли: пользователь с записью в журнале', $$
    INSERT INTO users (email, password_hash, role) VALUES ('erin@x.io', 'h', 'viewer');
    INSERT INTO audit_log (actor_id, action, entity_type)
        SELECT id, 'user.login', 'user' FROM users WHERE email = 'erin@x.io'
$$);
SELECT pg_temp.app_works('app: удаляет пользователя', $$ DELETE FROM users WHERE email = 'erin@x.io' $$);
SELECT pg_temp.expect('app: запись о удалённом пользователе осталась без актора',
    $$ SELECT count(*) FROM audit_log WHERE action = 'user.login' AND actor_id IS NULL $$, '1');


-- ===========================================================================
-- Каскады при удалении (ON DELETE)
-- ===========================================================================
-- У carol нет мониторов, зато есть канал; добавим токен и запись журнала.
SELECT pg_temp.works('каскады: данные пользователя без мониторов', $$
    INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)
    VALUES (pg_temp.u('carol'), gen_random_uuid(), pg_temp.h(100), '2026-02-01 00:00+00');
    INSERT INTO audit_log (actor_id, action, entity_type) VALUES (pg_temp.u('carol'), 'user.login', 'user');
$$);
DELETE FROM users WHERE email = 'carol@x.io';
SELECT pg_temp.expect('каскады: токены удалённого пользователя исчезли',
    $$ SELECT count(*) FROM refresh_tokens WHERE token_hash = pg_temp.h(100) $$, '0');
SELECT pg_temp.expect('каскады: каналы удалённого пользователя исчезли',
    $$ SELECT count(*) FROM channels WHERE name = 'carol-mail' $$, '0');
SELECT pg_temp.expect('каскады: журнал пережил пользователя, актор обнулён',
    $$ SELECT actor_id IS NULL FROM audit_log WHERE action = 'user.login' $$, 'true');

-- Удаление монитора уносит проверки, инциденты и подписки.
DELETE FROM monitors WHERE url = 'https://a.io' AND owner_id = pg_temp.u('alice');
SELECT pg_temp.expect('каскады: проверки удалённого монитора исчезли',
    $$ SELECT count(*) FROM checks WHERE url = 'https://a.io' AND monitor_id NOT IN (SELECT id FROM monitors) $$, '0');
SELECT pg_temp.expect('каскады: инциденты удалённого монитора исчезли',
    $$ SELECT count(*) FROM incidents WHERE monitor_id NOT IN (SELECT id FROM monitors) $$, '0');
SELECT pg_temp.expect('каскады: подписки удалённого монитора исчезли',
    $$ SELECT count(*) FROM monitor_channels WHERE monitor_id NOT IN (SELECT id FROM monitors) $$, '0');
SELECT pg_temp.expect('каскады: каналы владельца монитора остались',
    $$ SELECT count(*) FROM channels WHERE name IN ('ops-mail', 'hook') $$, '3');


-- ===========================================================================
\o
SELECT count(*) || ' проверок пройдено' AS result FROM passed;

ROLLBACK;
