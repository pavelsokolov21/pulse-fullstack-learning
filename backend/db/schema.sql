-- Схема Pulse для PostgreSQL 18.
--
-- ЭТО БАЗОВАЯ СХЕМА (миграция 0001). Применяет её Alembic: migrations/versions/
-- 0001_initial.py читает этот файл. После выпуска миграции файл НЕ ПРАВЯТ: любое
-- изменение схемы это новая миграция (`uv run alembic revision --autogenerate`).
--
-- Порядок применения (подробно в README):
--   1. roles.sql   суперпользователем: роли pulse_migrator (владелец, DDL) и
--                  pulse_app (приложение). Роли общие на весь кластер.
--   2. создать пустую базу с владельцем pulse_migrator.
--   3. `alembic upgrade head` ОТ ИМЕНИ pulse_migrator: он станет владельцем таблиц.
--      (Вручную тоже можно: psql -f schema.sql, но тогда Alembic не знает версию.)
-- Приложение подключается как pulse_app и не владеет ничем: права в конце файла.
-- Сбросить (только локально!):
--   DROP SCHEMA public CASCADE; CREATE SCHEMA public;
--
-- Принципы:
--  * Правила данных живут в самой БД (NOT NULL, UNIQUE, CHECK, FOREIGN KEY, EXCLUDE):
--    приложение их проверяет для понятных ошибок, но БД защищает от ошибок в коде,
--    ручных правок и второго клиента.
--  * Каждому ограничению дано имя: его видно в тексте ошибки и в будущих миграциях.
--  * У каждого внешнего ключа явно указано ON DELETE.
--  * Время только timestamptz (хранится в UTC), идентификаторы bigint IDENTITY.
--  * Индексы здесь только те, что нужны ограничениям, внешним ключам и самому
--    очевидному запросу. Остальные добавляем после EXPLAIN ANALYZE на данных.
--
-- Всё в одной транзакции: если что-то не создалось, не останется половины схемы.

BEGIN;

-- Нужно для EXCLUDE по (monitor_id =, диапазон &&) в incidents: обычный btree не
-- умеет сравнивать равенство вместе с пересечением диапазонов.
CREATE EXTENSION IF NOT EXISTS btree_gist;


-- ---------------------------------------------------------------------------
-- users: учётные записи
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         text        NOT NULL,
    password_hash text        NOT NULL,
    -- Без DEFAULT: забытое поле при регистрации не должно молча выдать права.
    -- Роль задаёт сервис (регистрация создаёт manager), БД требует её явно.
    role          text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT users_email_key UNIQUE (email),
    -- Приложение приводит email к нижнему регистру (normalize_email); БД не даёт
    -- обойти это: иначе Alice@x.io и alice@x.io стали бы двумя аккаунтами.
    -- Пробелы по краям отсекает users_email_format.
    CONSTRAINT users_email_normalized CHECK (email = lower(email)),
    CONSTRAINT users_email_format CHECK (
        email ~ '^[^@[:space:]]+@[^@[:space:]]+$' AND length(email) <= 254
    ),
    CONSTRAINT users_password_hash_not_blank CHECK (btrim(password_hash) <> ''),
    CONSTRAINT users_role_valid CHECK (role IN ('admin', 'manager', 'viewer'))
);
-- Роль это text + CHECK, а не тип ENUM: добавить значение в CHECK проще, чем
-- ALTER TYPE, и удалять значения из ENUM Postgres вообще не умеет.


-- ---------------------------------------------------------------------------
-- refresh_tokens: сессии (из недели 4 фазы 1; сейчас в памяти)
-- ---------------------------------------------------------------------------
CREATE TABLE refresh_tokens (
    id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Токен без пользователя бессмыслен: удаляем вместе с ним.
    user_id    bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- Цепочка одной сессии: при ротации новый токен наследует family_id.
    family_id  uuid        NOT NULL,
    -- Только sha256 токена (hex), сам токен в БД не попадает.
    token_hash text        NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,            -- NULL: токен ещё действителен
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT refresh_tokens_token_hash_key UNIQUE (token_hash),
    CONSTRAINT refresh_tokens_token_hash_sha256 CHECK (token_hash ~ '^[0-9a-f]{64}$')
);
-- Под внешний ключ (каскадное удаление и «отозвать все сессии пользователя»).
CREATE INDEX refresh_tokens_user_id_idx ON refresh_tokens (user_id);
-- Под отзыв всей цепочки при повторном использовании токена.
CREATE INDEX refresh_tokens_family_id_idx ON refresh_tokens (family_id);
-- Под регулярную чистку просроченных токенов (DELETE ... WHERE expires_at < now()).
CREATE INDEX refresh_tokens_expires_at_idx ON refresh_tokens (expires_at);


-- ---------------------------------------------------------------------------
-- monitors: что проверяем
-- ---------------------------------------------------------------------------
CREATE TABLE monitors (
    id              bigint           GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- RESTRICT: пользователя нельзя удалить, пока у него есть мониторы. Иначе одна
    -- команда DELETE FROM users молча унесла бы мониторы и всю историю проверок.
    -- Сначала мониторы удаляют или передают другому владельцу, явно.
    owner_id        bigint           NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    url             text             NOT NULL,
    -- Целые секунды, а не тип interval: у interval есть месяцы и дни без фиксированной
    -- длины, а секунды однозначны. В приложении то же представление: в JSON
    -- секундами (IntervalSeconds), в домене timedelta, преобразование только на
    -- границе репозитория.
    interval_seconds integer         NOT NULL,
    timeout_seconds double precision NOT NULL DEFAULT 10,
    created_at      timestamptz      NOT NULL DEFAULT now(),

    -- Уникальность URL в рамках владельца (перенос из ревью недели 2 фазы 1).
    CONSTRAINT monitors_owner_url_key UNIQUE (owner_id, url),
    -- Нужен как цель составного внешнего ключа из monitor_channels (см. ниже).
    CONSTRAINT monitors_id_owner_key UNIQUE (id, owner_id),
    CONSTRAINT monitors_url_format CHECK (url ~ '^https?://[^[:space:]]+$' AND length(url) <= 2048),
    -- Границы те же, что в приложении (MIN_INTERVAL..MAX_INTERVAL в domain/types.py).
    CONSTRAINT monitors_interval_range CHECK (interval_seconds BETWEEN 30 AND 86400),
    CONSTRAINT monitors_timeout_positive CHECK (timeout_seconds > 0),
    -- Таймаут короче интервала, иначе проверки одного монитора накладываются друг на
    -- друга. Заодно отсекает NaN и Infinity: в Postgres NaN «больше» любого числа
    -- (в том числе бесконечности), поэтому ни то ни другое не бывает меньше
    -- интервала. Одного «timeout > 0» NaN бы не остановил.
    CONSTRAINT monitors_timeout_below_interval CHECK (timeout_seconds < interval_seconds)
);
-- Индекс под owner_id уже даёт UNIQUE (owner_id, url): отдельный не нужен.


-- ---------------------------------------------------------------------------
-- checks: результаты проверок (самая большая таблица)
-- ---------------------------------------------------------------------------
CREATE TABLE checks (
    id          bigint           GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Проверки принадлежат монитору: удаляем вместе с ним (как сейчас в сервисе).
    monitor_id  bigint           NOT NULL REFERENCES monitors (id) ON DELETE CASCADE,
    -- Снимок адреса на момент проверки: после смены URL у монитора история не
    -- начинает относиться к чужому сайту. Осознанная денормализация.
    url         text             NOT NULL,
    status_code smallint,                      -- NULL: ответа не было
    latency_ms  double precision NOT NULL,
    error       text,                          -- NULL: ответ получен
    checked_at  timestamptz      NOT NULL,
    -- Правило «успех = ответ 200..399» хранится в одном месте и не может разойтись
    -- со status_code. STORED, а не VIRTUAL: в PG 18 виртуальный столбец по умолчанию
    -- и по нему нельзя строить индекс.
    ok          boolean          GENERATED ALWAYS AS (
                    COALESCE(status_code BETWEEN 200 AND 399, false)
                ) STORED,

    -- Цель составного внешнего ключа из incidents: инцидент может ссылаться только
    -- на проверку СВОЕГО монитора. Цена: ещё один индекс на самой большой таблице.
    CONSTRAINT checks_id_monitor_key UNIQUE (id, monitor_id),
    CONSTRAINT checks_url_format CHECK (url ~ '^https?://[^[:space:]]+$' AND length(url) <= 2048),
    CONSTRAINT checks_status_code_range CHECK (status_code BETWEEN 100 AND 599),
    CONSTRAINT checks_latency_finite_non_negative CHECK (
        latency_ms >= 0 AND latency_ms < 'Infinity'::double precision
    ),
    -- Либо получили ответ (есть код, ошибки нет), либо не получили (кода нет, есть
    -- текст ошибки). Остальные сочетания значат баг в записи.
    CONSTRAINT checks_status_xor_error CHECK ((status_code IS NULL) = (error IS NOT NULL)),
    CONSTRAINT checks_error_not_blank CHECK (error IS NULL OR btrim(error) <> '')
);
-- Главный запрос: последние проверки монитора (list_recent). Заодно покрывает
-- внешний ключ monitor_id. Замер на реальном объёме сделаем при оптимизации.
CREATE INDEX checks_monitor_id_checked_at_idx ON checks (monitor_id, checked_at DESC);


-- ---------------------------------------------------------------------------
-- incidents: серии неудачных проверок
-- ---------------------------------------------------------------------------
CREATE TABLE incidents (
    id                   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    monitor_id           bigint      NOT NULL REFERENCES monitors (id) ON DELETE CASCADE,
    started_at           timestamptz NOT NULL,
    resolved_at          timestamptz,           -- NULL: инцидент ещё открыт
    failed_checks        integer     NOT NULL DEFAULT 1,
    last_error           text,
    -- Проверка, открывшая инцидент, и проверка, закрывшая его (ключи ниже).
    opened_by_check_id   bigint,
    resolved_by_check_id bigint,
    created_at           timestamptz NOT NULL DEFAULT now(),

    -- Составные ключи (проверка, монитор): проверка обязана принадлежать тому же
    -- монитору, что и инцидент. ON DELETE SET NULL (столбец) обнуляет только
    -- ссылку на проверку (PG 15+), а monitor_id остаётся: простой SET NULL
    -- обнулил бы и его. Старые проверки удаляются по сроку хранения (приложение
    -- держит последние N), а инцидент должен остаться. При NULL в ссылке ключ не
    -- проверяется (MATCH SIMPLE).
    CONSTRAINT incidents_opened_by_check_fkey
        FOREIGN KEY (opened_by_check_id, monitor_id)
        REFERENCES checks (id, monitor_id) ON DELETE SET NULL (opened_by_check_id),
    CONSTRAINT incidents_resolved_by_check_fkey
        FOREIGN KEY (resolved_by_check_id, monitor_id)
        REFERENCES checks (id, monitor_id) ON DELETE SET NULL (resolved_by_check_id),
    CONSTRAINT incidents_failed_checks_positive CHECK (failed_checks >= 1),
    -- Строго «после»: нулевая длительность давала бы пустой диапазон, а пустые
    -- диапазоны EXCLUDE ниже ни с чем не сравнивает.
    CONSTRAINT incidents_resolved_after_started CHECK (
        resolved_at IS NULL OR resolved_at > started_at
    ),
    CONSTRAINT incidents_last_error_not_blank CHECK (last_error IS NULL OR btrim(last_error) <> ''),
    -- У монитора не бывает двух инцидентов одновременно. Диапазон с NULL в конце
    -- не ограничен справа, поэтому этим же правилом запрещён и второй открытый
    -- инцидент (то же, что дал бы частичный UNIQUE ... WHERE resolved_at IS NULL).
    CONSTRAINT incidents_no_overlap EXCLUDE USING gist (
        monitor_id WITH =,
        tstzrange(started_at, resolved_at) WITH &&
    )
);
-- EXCLUDE строит gist-индекс по monitor_id, так что внешний ключ покрыт.
-- Под внешние ключи на checks (иначе удаление проверки сканирует всю таблицу):
CREATE INDEX incidents_opened_by_check_id_idx ON incidents (opened_by_check_id)
    WHERE opened_by_check_id IS NOT NULL;
CREATE INDEX incidents_resolved_by_check_id_idx ON incidents (resolved_by_check_id)
    WHERE resolved_by_check_id IS NOT NULL;


-- ---------------------------------------------------------------------------
-- channels: куда слать уведомления
-- ---------------------------------------------------------------------------
CREATE TABLE channels (
    id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Канал это настройка пользователя без собственной истории: удаляем вместе с ним.
    owner_id   bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name       text        NOT NULL,
    kind       text        NOT NULL,
    target     text        NOT NULL,   -- адрес почты или URL вебхука
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT channels_owner_name_key UNIQUE (owner_id, name),
    -- Цель составного внешнего ключа из monitor_channels.
    CONSTRAINT channels_id_owner_key UNIQUE (id, owner_id),
    CONSTRAINT channels_name_not_blank CHECK (btrim(name) <> '' AND length(name) <= 100),
    CONSTRAINT channels_kind_valid CHECK (kind IN ('email', 'webhook', 'slack')),
    -- Формат цели зависит от вида канала.
    CONSTRAINT channels_target_matches_kind CHECK (
        CASE kind
            WHEN 'email' THEN target ~ '^[^@[:space:]]+@[^@[:space:]]+$'
            ELSE target ~ '^https://[^[:space:]]+$'
        END
        AND length(target) <= 2048
    )
);


-- ---------------------------------------------------------------------------
-- monitor_channels: какие каналы уведомляют о каком мониторе (многие-ко-многим)
-- ---------------------------------------------------------------------------
CREATE TABLE monitor_channels (
    monitor_id bigint      NOT NULL,
    channel_id bigint      NOT NULL,
    -- Владелец продублирован намеренно: через два составных внешних ключа БД сама
    -- гарантирует, что монитор и канал принадлежат ОДНОМУ пользователю. Иначе
    -- ошибка в коде позволила бы подписать чужой канал на свой монитор, то же
    -- нарушение владения, что и IDOR, только на уровне данных.
    owner_id   bigint      NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT monitor_channels_pkey PRIMARY KEY (monitor_id, channel_id),
    CONSTRAINT monitor_channels_monitor_fkey FOREIGN KEY (monitor_id, owner_id)
        REFERENCES monitors (id, owner_id) ON DELETE CASCADE,
    CONSTRAINT monitor_channels_channel_fkey FOREIGN KEY (channel_id, owner_id)
        REFERENCES channels (id, owner_id) ON DELETE CASCADE
);
-- PRIMARY KEY (monitor_id, channel_id) покрывает поиск каналов монитора;
-- обратный поиск «на какие мониторы подписан канал» и удаление канала:
CREATE INDEX monitor_channels_channel_id_idx ON monitor_channels (channel_id);


-- ---------------------------------------------------------------------------
-- audit_log: кто, что и когда сделал (только дописывается)
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    -- SET NULL: журнал переживает удаление пользователя. NULL значит «система»
    -- или «пользователь удалён».
    actor_id    bigint      REFERENCES users (id) ON DELETE SET NULL,
    action      text        NOT NULL,   -- 'monitor.create', 'user.login_failed', ...
    entity_type text        NOT NULL,
    -- Без внешнего ключа намеренно: запись ссылается на объекты разных таблиц
    -- (полиморфная ссылка), и журнал должен жить дольше удалённого объекта.
    entity_id   bigint,
    details     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    request_id  text,                   -- X-Request-ID: связывает запись с логами
    ip          inet,

    CONSTRAINT audit_log_action_format CHECK (action ~ '^[a-z]+(_[a-z]+)*\.[a-z]+(_[a-z]+)*$'),
    CONSTRAINT audit_log_entity_type_valid CHECK (
        entity_type IN ('user', 'monitor', 'channel', 'check', 'incident', 'session')
    ),
    CONSTRAINT audit_log_details_is_object CHECK (jsonb_typeof(details) = 'object'),
    CONSTRAINT audit_log_request_id_not_blank CHECK (request_id IS NULL OR btrim(request_id) <> '')
);
-- Под внешний ключ и вопрос «что делал пользователь».
CREATE INDEX audit_log_actor_id_occurred_at_idx ON audit_log (actor_id, occurred_at DESC)
    WHERE actor_id IS NOT NULL;
-- Под вопрос «что происходило с этим объектом».
CREATE INDEX audit_log_entity_idx ON audit_log (entity_type, entity_id, occurred_at DESC);


-- ---------------------------------------------------------------------------
-- Права приложения (pulse_app): минимально необходимые
-- ---------------------------------------------------------------------------
-- Приложение не владеет таблицами, поэтому не может ни изменить схему, ни
-- выдать себе права. Владелец (pulse_migrator) нужен только миграциям.
GRANT USAGE ON SCHEMA public TO pulse_app;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON users, refresh_tokens, monitors, incidents, channels, monitor_channels
    TO pulse_app;

-- Результаты проверок не правят; DELETE нужен для хранения последних N.
GRANT SELECT, INSERT, DELETE ON checks TO pulse_app;

-- Журнал только дописывается и читается: UPDATE и DELETE приложению недоступны,
-- то есть и взломанное приложение не перепишет историю. Каскад ON DELETE SET NULL
-- по actor_id при удалении пользователя это не нарушает: ссылочные действия
-- выполняются с правами владельца таблицы.
GRANT SELECT, INSERT ON audit_log TO pulse_app;
-- Для INSERT в столбцы IDENTITY права на последовательности не требуются.

COMMIT;
