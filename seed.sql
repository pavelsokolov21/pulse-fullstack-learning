DROP TABLE IF EXISTS checks, monitors;

CREATE TABLE monitors (
    id          serial PRIMARY KEY,
    url         text NOT NULL UNIQUE,
    interval_s  int  NOT NULL
);

CREATE TABLE checks (
    id          bigserial PRIMARY KEY,
    monitor_id  int NOT NULL REFERENCES monitors(id),
    checked_at  timestamptz NOT NULL,
    status_code int,            -- NULL: ответа не было
    latency_ms  numeric(8,1) NOT NULL,
    error       text
);

INSERT INTO monitors (url, interval_s) VALUES
    ('https://alpha.example', 300), ('https://beta.example', 300),
    ('https://gamma.example', 300), ('https://delta.example', 300),
    ('https://idle.example', 300);   -- у этого проверок не будет

SELECT setseed(0.42);

INSERT INTO checks (monitor_id, checked_at, status_code, latency_ms, error)
SELECT
    m.id,
    now() - (n * interval '5 minutes'),
    CASE
        WHEN n % 97 < 6                THEN NULL      -- серия из 6 подряд: инцидент
        WHEN random() < 0.02           THEN 503       -- одиночные сбои: «дребезг»
        ELSE 200
    END,
    round((40 + m.id * 25 + random() * 120 + CASE WHEN random() < 0.03 THEN 900 ELSE 0 END)::numeric, 1),
    CASE WHEN n % 97 < 6 THEN 'timeout' END
FROM monitors m
CROSS JOIN generate_series(0, 863) AS n          -- 3 суток по 5 минут
WHERE m.url <> 'https://idle.example';