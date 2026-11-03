"""Генератор тестовых данных Pulse на Faker.

Чистые функции: с базой не работают, только строят строки для всех таблиц схемы
(db/schema.sql). Загрузку выполняет db/seed.py. Разделение нужно, чтобы правила
данных проверялись быстро, без БД (tests/db/test_seed_data.py).

Данные нужны не для красоты, а чтобы схема выдержала правдоподобную нагрузку:
у мониторов есть история проверок с отказами сериями, из серий строятся
инциденты, каналы привязаны к мониторам своего владельца, журнал аудита
согласован с остальными таблицами. Результат полностью определяется `seed` и
`now`: два запуска с одинаковыми значениями дают одинаковые строки.

Строки ссылаются друг на друга позициями в списках (`owner_ref`, `monitor_ref`):
настоящие id выдаёт БД, загрузчик подставляет их сам.
"""

import hashlib
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from faker import Faker

ADMIN_EMAIL = "admin@example.com"

# Интервалы и таймауты укладываются в правила схемы: 30 с..24 ч, таймаут короче.
INTERVALS = (30, 60, 300, 600, 900, 3600)
# Самый длинный таймаут короче самого короткого интервала: правило схемы
# «таймаут < интервала» выполняется для любой пары (проверяет тест).
TIMEOUTS = (2.0, 5.0, 10.0, 15.0, 20.0)
CHANNEL_KINDS = ("email", "webhook", "slack")

# Сколько неудачных проверок подряд открывают инцидент.
INCIDENT_THRESHOLD = 3
# Историю длиннее не строим: при интервале в 30 с это были бы сотни тысяч строк.
MAX_HISTORY = timedelta(days=14)
SESSION_TTL = timedelta(days=7)

NETWORK_ERRORS = (
    "Network Error: ConnectTimeout",
    "Network Error: ReadTimeout",
    "Network Error: ConnectError",
)


@dataclass(frozen=True)
class SeedConfig:
    users: int = 30
    checks_per_monitor: int = 200
    seed: int = 42
    # Для тестов: с фиксированным `now` результат воспроизводится дословно.
    now: datetime | None = None


@dataclass(frozen=True)
class UserRow:
    email: str
    password_hash: str
    role: str
    created_at: datetime


@dataclass(frozen=True)
class RefreshTokenRow:
    user_ref: int
    family_id: uuid.UUID
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class ChannelRow:
    owner_ref: int
    name: str
    kind: str
    target: str
    created_at: datetime


@dataclass(frozen=True)
class MonitorRow:
    owner_ref: int
    url: str
    interval_seconds: int
    timeout_seconds: float
    created_at: datetime


@dataclass(frozen=True)
class MonitorChannelRow:
    monitor_ref: int
    channel_ref: int
    owner_ref: int


@dataclass(frozen=True)
class CheckRow:
    monitor_ref: int
    url: str
    status_code: int | None
    latency_ms: float
    error: str | None
    checked_at: datetime

    @property
    def ok(self) -> bool:
        # То же правило, что у генерируемого столбца checks.ok в схеме.
        return self.status_code is not None and 200 <= self.status_code <= 399


@dataclass(frozen=True)
class IncidentRow:
    monitor_ref: int
    started_at: datetime
    resolved_at: datetime | None
    failed_checks: int
    last_error: str
    opened_by_check_ref: int
    resolved_by_check_ref: int | None


@dataclass(frozen=True)
class AuditRow:
    occurred_at: datetime
    actor_ref: int | None  # None: действие системы
    action: str
    entity_type: str
    entity_ref: int | None  # позиция в списке сущности entity_type
    details: dict[str, object]
    request_id: str
    ip: str


@dataclass(frozen=True)
class SeedData:
    users: list[UserRow]
    refresh_tokens: list[RefreshTokenRow]
    channels: list[ChannelRow]
    monitors: list[MonitorRow]
    monitor_channels: list[MonitorChannelRow]
    checks: list[CheckRow]
    incidents: list[IncidentRow]
    audit_log: list[AuditRow]

    def counts(self) -> dict[str, int]:
        return {
            "users": len(self.users),
            "refresh_tokens": len(self.refresh_tokens),
            "channels": len(self.channels),
            "monitors": len(self.monitors),
            "monitor_channels": len(self.monitor_channels),
            "checks": len(self.checks),
            "incidents": len(self.incidents),
            "audit_log": len(self.audit_log),
        }


def generate(config: SeedConfig, password_hash: str) -> SeedData:
    """Строит данные для всех таблиц. `password_hash` один на всех пользователей."""
    if config.users < 1:
        raise ValueError("нужен хотя бы один пользователь (админ)")
    now = config.now or datetime.now(UTC)
    rng = random.Random(config.seed)
    fake = Faker("en_US")
    fake.seed_instance(config.seed)

    users = _users(rng, fake, config.users, now, password_hash)
    tokens = _refresh_tokens(rng, users, now)
    channels = _channels(rng, fake, users)
    monitors, checks, incidents = _monitors_with_history(
        rng, fake, users, now, config.checks_per_monitor
    )
    links = _monitor_channels(rng, monitors, channels)
    audit = _audit_log(rng, fake, now, users, monitors, channels, checks, incidents)
    return SeedData(users, tokens, channels, monitors, links, checks, incidents, audit)


# --- users ------------------------------------------------------------------


def _users(
    rng: random.Random, fake: Faker, count: int, now: datetime, password_hash: str
) -> list[UserRow]:
    rows: list[UserRow] = []
    seen: set[str] = set()
    for index in range(count):
        if index == 0:
            email, role = ADMIN_EMAIL, "admin"
        else:
            email = fake.safe_email().lower()
            while email in seen or email == ADMIN_EMAIL:
                email = fake.safe_email().lower()
            role = "viewer" if rng.random() < 0.15 else "manager"
        seen.add(email)
        created_at = now - timedelta(days=rng.uniform(30, 120))
        rows.append(UserRow(email, password_hash, role, created_at))
    return rows


# --- refresh_tokens ---------------------------------------------------------


def _refresh_tokens(
    rng: random.Random, users: list[UserRow], now: datetime
) -> list[RefreshTokenRow]:
    rows: list[RefreshTokenRow] = []
    for user_ref in range(len(users)):
        for _ in range(rng.randint(0, 3)):  # сессий на пользователя
            family_id = uuid.UUID(int=rng.getrandbits(128), version=4)
            created = now - timedelta(hours=rng.uniform(1, 240))
            chain: list[datetime] = []
            for _ in range(rng.randint(1, 4)):  # токенов в цепочке (ротаций)
                if created > now:
                    break
                chain.append(created)
                created += timedelta(hours=rng.uniform(1, 6))
            # Цепочку могли отозвать целиком (повторное использование токена).
            chain_revoked = rng.random() < 0.12
            for position, issued_at in enumerate(chain):
                is_last = position == len(chain) - 1
                if not is_last:
                    revoked_at = chain[position + 1]  # заменён следующим
                elif chain_revoked:
                    revoked_at = min(now, issued_at + timedelta(minutes=30))
                else:
                    revoked_at = None  # действует (или просрочен по expires_at)
                token = rng.getrandbits(256).to_bytes(32, "big")
                rows.append(
                    RefreshTokenRow(
                        user_ref=user_ref,
                        family_id=family_id,
                        token_hash=hashlib.sha256(token).hexdigest(),
                        expires_at=issued_at + SESSION_TTL,
                        revoked_at=revoked_at,
                        created_at=issued_at,
                    )
                )
    return rows


# --- channels ---------------------------------------------------------------


def _channels(
    rng: random.Random, fake: Faker, users: list[UserRow]
) -> list[ChannelRow]:
    rows: list[ChannelRow] = []
    for owner_ref, user in enumerate(users):
        names: set[str] = set()
        for _ in range(rng.randint(1, 4)):
            kind = rng.choices(CHANNEL_KINDS, weights=(50, 25, 25))[0]
            name = f"{kind}-{fake.word()}".lower()
            while name in names:
                name = f"{kind}-{fake.word()}-{rng.randint(2, 99)}".lower()
            names.add(name)
            rows.append(
                ChannelRow(
                    owner_ref=owner_ref,
                    name=name,
                    kind=kind,
                    target=_channel_target(rng, fake, kind),
                    created_at=user.created_at + timedelta(days=rng.uniform(0, 5)),
                )
            )
    return rows


def _channel_target(rng: random.Random, fake: Faker, kind: str) -> str:
    if kind == "email":
        return str(fake.company_email()).lower()
    if kind == "webhook":
        return f"https://{fake.domain_name()}/hooks/{rng.getrandbits(32):08x}"
    secret = "".join(rng.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=24))
    return f"https://hooks.slack.com/services/T{rng.getrandbits(24):06X}/{secret}"


# --- monitors, checks, incidents --------------------------------------------


def _monitors_with_history(
    rng: random.Random,
    fake: Faker,
    users: list[UserRow],
    now: datetime,
    checks_per_monitor: int,
) -> tuple[list[MonitorRow], list[CheckRow], list[IncidentRow]]:
    monitors: list[MonitorRow] = []
    checks: list[CheckRow] = []
    incidents: list[IncidentRow] = []
    for owner_ref, user in enumerate(users):
        urls: set[str] = set()
        # Наблюдатели почти ничего не создают: роль только для чтения.
        count = rng.randint(0, 2) if user.role == "viewer" else rng.randint(2, 8)
        for _ in range(count):
            url = _monitor_url(rng, fake)
            while url in urls:
                url = _monitor_url(rng, fake)
            urls.add(url)

            interval = rng.choice(INTERVALS)
            timeout = rng.choice(TIMEOUTS)  # всегда короче любого интервала
            history = min(
                checks_per_monitor, int(MAX_HISTORY.total_seconds() // interval)
            )
            monitor_ref = len(monitors)
            series = _check_series(
                rng, monitor_ref, url, interval, timeout, now, history
            )
            first_check = series[0].checked_at if series else now
            monitors.append(
                MonitorRow(
                    owner_ref=owner_ref,
                    url=url,
                    interval_seconds=interval,
                    timeout_seconds=timeout,
                    created_at=first_check - timedelta(hours=rng.uniform(1, 24)),
                )
            )
            incidents.extend(_incidents_from(series, monitor_ref, len(checks)))
            checks.extend(series)
    return monitors, checks, incidents


def _monitor_url(rng: random.Random, fake: Faker) -> str:
    scheme = "https" if rng.random() < 0.9 else "http"
    path = rng.choice(("", "/health", "/status", "/api/ping", f"/{fake.slug()}"))
    return f"{scheme}://{fake.domain_name()}{path}".lower()


def _check_series(
    rng: random.Random,
    monitor_ref: int,
    url: str,
    interval: int,
    timeout: float,
    now: datetime,
    count: int,
) -> list[CheckRow]:
    """История проверок монитора по возрастанию времени.

    Отказы идут сериями (аварии на 3-10 проверок) плюс одиночные сбои. Часть
    мониторов «лежит» прямо сейчас: серия не закончилась, инцидент открыт.
    """
    base_latency = rng.uniform(40, 400)
    down_now = rng.random() < 0.10
    tail = rng.randint(INCIDENT_THRESHOLD, 6) if down_now else 0
    outage_left = 0
    outage_is_timeout = True

    rows: list[CheckRow] = []
    for index in range(count):
        # Небольшой разброс времени, как у настоящего планировщика; шаг всё равно
        # больше нуля (интервал не меньше 30 с, разброс до 3 с).
        offset = (count - 1 - index) * interval + rng.uniform(0, 3)
        checked_at = now - timedelta(seconds=offset)

        in_forced_tail = index >= count - tail
        if outage_left == 0 and (in_forced_tail or rng.random() < 0.005):
            outage_left = rng.randint(INCIDENT_THRESHOLD, 10)
            outage_is_timeout = rng.random() < 0.5
        if outage_left > 0:
            outage_left -= 1
            rows.append(
                _failure(rng, monitor_ref, url, timeout, checked_at, outage_is_timeout)
            )
        elif rng.random() < 0.015:  # одиночный сбой («дребезг»)
            rows.append(
                _failure(rng, monitor_ref, url, timeout, checked_at, rng.random() < 0.5)
            )
        else:
            status = rng.choices((200, 301, 302), weights=(94, 3, 3))[0]
            latency = base_latency * rng.lognormvariate(0, 0.3)
            rows.append(
                CheckRow(
                    monitor_ref,
                    url,
                    status,
                    max(1.0, round(latency, 1)),
                    None,
                    checked_at,
                )
            )
    return rows


def _failure(
    rng: random.Random,
    monitor_ref: int,
    url: str,
    timeout: float,
    checked_at: datetime,
    is_timeout: bool,
) -> CheckRow:
    if is_timeout:
        # Ответа не было: кода нет, есть текст ошибки, ждали весь таймаут.
        latency = round(timeout * 1000 + rng.uniform(0, 50), 1)
        return CheckRow(
            monitor_ref, url, None, latency, rng.choice(NETWORK_ERRORS), checked_at
        )
    # Ответ получен, но плохой: код есть, ошибки нет.
    status = rng.choice((404, 500, 502, 503, 504))
    return CheckRow(
        monitor_ref, url, status, round(rng.uniform(20, 900), 1), None, checked_at
    )


def _incidents_from(
    series: list[CheckRow], monitor_ref: int, base: int
) -> list[IncidentRow]:
    """Инциденты монитора: серии неудачных проверок не короче порога.

    `base` смещение проверок этого монитора в общем списке проверок.
    """
    incidents: list[IncidentRow] = []
    index = 0
    while index < len(series):
        if series[index].ok:
            index += 1
            continue
        end = index
        while end < len(series) and not series[end].ok:
            end += 1
        run = end - index
        if run >= INCIDENT_THRESHOLD:
            last = series[end - 1]
            resolved = end < len(series)
            incidents.append(
                IncidentRow(
                    monitor_ref=monitor_ref,
                    started_at=series[index].checked_at,
                    resolved_at=series[end].checked_at if resolved else None,
                    failed_checks=run,
                    last_error=last.error or f"HTTP {last.status_code}",
                    # Инцидент открывает проверка, на которой набрался порог.
                    opened_by_check_ref=base + index + INCIDENT_THRESHOLD - 1,
                    resolved_by_check_ref=base + end if resolved else None,
                )
            )
        index = end
    return incidents


# --- monitor_channels -------------------------------------------------------


def _monitor_channels(
    rng: random.Random, monitors: list[MonitorRow], channels: list[ChannelRow]
) -> list[MonitorChannelRow]:
    by_owner: dict[int, list[int]] = {}
    for channel_ref, channel in enumerate(channels):
        by_owner.setdefault(channel.owner_ref, []).append(channel_ref)

    rows: list[MonitorChannelRow] = []
    for monitor_ref, monitor in enumerate(monitors):
        own = by_owner.get(monitor.owner_ref, [])
        # Только каналы владельца монитора: БД это и так проверит составным ключом.
        for channel_ref in rng.sample(own, rng.randint(0, min(3, len(own)))):
            rows.append(MonitorChannelRow(monitor_ref, channel_ref, monitor.owner_ref))
    return rows


# --- audit_log --------------------------------------------------------------


def _audit_log(
    rng: random.Random,
    fake: Faker,
    now: datetime,
    users: list[UserRow],
    monitors: list[MonitorRow],
    channels: list[ChannelRow],
    checks: list[CheckRow],
    incidents: list[IncidentRow],
) -> list[AuditRow]:
    events: list[AuditRow] = []

    def add(
        occurred_at: datetime,
        actor_ref: int | None,
        action: str,
        entity_type: str,
        entity_ref: int | None,
        **details: object,
    ) -> None:
        events.append(
            AuditRow(
                occurred_at=occurred_at,
                actor_ref=actor_ref,
                action=action,
                entity_type=entity_type,
                entity_ref=entity_ref,
                details=details,
                request_id=f"{rng.getrandbits(128):032x}",
                ip=str(fake.ipv4()),
            )
        )

    def random_moment(since: datetime) -> datetime:
        return since + (now - since) * rng.random()

    for ref, user in enumerate(users):
        add(user.created_at, ref, "user.register", "user", ref, role=user.role)
        for _ in range(rng.randint(1, 6)):
            agent = str(fake.user_agent())
            add(
                random_moment(user.created_at),
                ref,
                "user.login",
                "session",
                None,
                user_agent=agent,
            )
        for _ in range(rng.randint(0, 2)):
            add(random_moment(user.created_at), None, "user.login_failed", "user", ref)

    for ref, channel in enumerate(channels):
        add(
            channel.created_at,
            channel.owner_ref,
            "channel.create",
            "channel",
            ref,
            kind=channel.kind,
        )

    for ref, monitor in enumerate(monitors):
        add(
            monitor.created_at,
            monitor.owner_ref,
            "monitor.create",
            "monitor",
            ref,
            url=monitor.url,
        )
        if rng.random() < 0.3:
            add(
                random_moment(monitor.created_at),
                monitor.owner_ref,
                "monitor.update",
                "monitor",
                ref,
                fields=["timeout_seconds"],
            )

    for ref, incident in enumerate(incidents):
        opened = checks[incident.opened_by_check_ref].checked_at
        add(
            opened,
            None,
            "incident.open",
            "incident",
            ref,
            failed_checks=INCIDENT_THRESHOLD,
        )
        if incident.resolved_at is not None:
            add(
                incident.resolved_at,
                None,
                "incident.resolve",
                "incident",
                ref,
                failed_checks=incident.failed_checks,
            )

    # Журнал дописывается по времени: id в БД должны идти в хронологическом порядке.
    events.sort(key=lambda event: event.occurred_at)
    return events
