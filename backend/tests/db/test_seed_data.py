import ipaddress
import math
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest
from seed_data import (
    ADMIN_EMAIL,
    INCIDENT_THRESHOLD,
    INTERVALS,
    MAX_HISTORY,
    SESSION_TTL,
    TIMEOUTS,
    IncidentRow,
    RefreshTokenRow,
    SeedConfig,
    SeedData,
    generate,
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
HASH = "hash-of-demo-password"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")
URL = re.compile(r"^https?://\S+$")
ACTION = re.compile(r"^[a-z]+(_[a-z]+)*\.[a-z]+(_[a-z]+)*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def make(**overrides: Any) -> SeedData:
    values: dict[str, Any] = {
        "users": 40,
        "checks_per_monitor": 120,
        "seed": 7,
        "now": NOW,
        **overrides,
    }
    return generate(SeedConfig(**values), HASH)


@pytest.fixture(scope="module")
def data() -> SeedData:
    return make()


# --- воспроизводимость и объём ---------------------------------------------


def test_same_seed_gives_identical_data() -> None:
    assert make() == make()


def test_different_seed_gives_different_data() -> None:
    assert make(seed=8) != make()


def test_counts_cover_every_table(data: SeedData) -> None:
    counts = data.counts()

    assert counts["users"] == 40
    assert all(count > 0 for count in counts.values()), counts


def test_a_single_user_is_the_admin() -> None:
    only = make(users=1)

    assert [(u.email, u.role) for u in only.users] == [(ADMIN_EMAIL, "admin")]


def test_zero_users_is_rejected() -> None:
    with pytest.raises(ValueError, match="хотя бы один пользователь"):
        make(users=0)


def test_zero_checks_means_no_checks_and_no_incidents() -> None:
    empty = make(checks_per_monitor=0)

    assert empty.checks == []
    assert empty.incidents == []
    assert empty.monitors  # мониторы всё равно есть


# --- users ------------------------------------------------------------------


def test_users_follow_the_schema_rules(data: SeedData) -> None:
    emails = [u.email for u in data.users]

    assert emails[0] == ADMIN_EMAIL
    assert len(set(emails)) == len(emails)
    assert all(e == e.lower() and EMAIL.match(e) and len(e) <= 254 for e in emails)
    assert {u.role for u in data.users} <= {"admin", "manager", "viewer"}
    assert [u.role for u in data.users].count("admin") == 1
    assert {u.password_hash for u in data.users} == {HASH}
    assert all(u.created_at < NOW for u in data.users)


def test_all_roles_are_represented(data: SeedData) -> None:
    assert {u.role for u in data.users} == {"admin", "manager", "viewer"}


# --- refresh_tokens ---------------------------------------------------------


def test_refresh_tokens_follow_the_schema_rules(data: SeedData) -> None:
    hashes = [t.token_hash for t in data.refresh_tokens]

    assert len(set(hashes)) == len(hashes)
    assert all(SHA256.match(h) for h in hashes)
    assert all(t.created_at <= NOW for t in data.refresh_tokens)
    assert all(t.expires_at == t.created_at + SESSION_TTL for t in data.refresh_tokens)


def test_token_chains_are_rotated_and_belong_to_one_user(data: SeedData) -> None:
    chains: defaultdict[uuid.UUID, list[RefreshTokenRow]] = defaultdict(list)
    for token in data.refresh_tokens:
        chains[token.family_id].append(token)

    assert chains
    for chain in chains.values():
        assert len({t.user_ref for t in chain}) == 1
        ordered = sorted(chain, key=lambda t: t.created_at)
        # Заменённый токен отозван в момент выпуска следующего.
        for older, newer in pairwise(ordered):
            assert older.revoked_at == newer.created_at


def test_some_sessions_are_alive_and_some_revoked(data: SeedData) -> None:
    last_of_chain: dict[uuid.UUID, RefreshTokenRow] = {}
    for token in sorted(data.refresh_tokens, key=lambda t: t.created_at):
        last_of_chain[token.family_id] = token

    assert any(t.revoked_at is None for t in last_of_chain.values())
    assert any(t.revoked_at is not None for t in last_of_chain.values())


# --- channels, monitors, links ---------------------------------------------


def test_channels_follow_the_schema_rules(data: SeedData) -> None:
    names = [(c.owner_ref, c.name) for c in data.channels]

    assert len(set(names)) == len(names)
    assert {c.kind for c in data.channels} == {"email", "webhook", "slack"}
    for channel in data.channels:
        assert channel.name.strip()
        assert len(channel.name) <= 100
        if channel.kind == "email":
            assert EMAIL.match(channel.target)
        else:
            assert channel.target.startswith("https://")
            assert " " not in channel.target
        assert len(channel.target) <= 2048


def test_monitors_follow_the_schema_rules(data: SeedData) -> None:
    keys = [(m.owner_ref, m.url) for m in data.monitors]

    assert len(set(keys)) == len(keys)
    for monitor in data.monitors:
        assert URL.match(monitor.url)
        assert len(monitor.url) <= 2048
        assert 30 <= monitor.interval_seconds <= 86400
        assert monitor.interval_seconds in INTERVALS
        # Те же правила, что monitors_timeout_*: положительный, строго короче интервала.
        assert 0 < monitor.timeout_seconds < monitor.interval_seconds
        assert data.users[monitor.owner_ref].created_at <= monitor.created_at


def test_every_monitor_is_older_than_its_first_check(data: SeedData) -> None:
    first_check: dict[int, datetime] = {}
    for check in data.checks:
        first_check.setdefault(check.monitor_ref, check.checked_at)

    for monitor_ref, started in first_check.items():
        assert data.monitors[monitor_ref].created_at < started


def test_links_connect_only_one_owners_monitor_and_channel(data: SeedData) -> None:
    pairs = [(link.monitor_ref, link.channel_ref) for link in data.monitor_channels]

    assert data.monitor_channels
    assert len(set(pairs)) == len(pairs)
    for link in data.monitor_channels:
        owner = data.monitors[link.monitor_ref].owner_ref
        assert data.channels[link.channel_ref].owner_ref == owner == link.owner_ref


# --- checks -----------------------------------------------------------------


def test_checks_follow_the_schema_rules(data: SeedData) -> None:
    for check in data.checks:
        assert check.url == data.monitors[check.monitor_ref].url
        assert math.isfinite(check.latency_ms)
        assert check.latency_ms >= 0
        assert check.checked_at <= NOW
        if check.status_code is None:
            # Ответа не было: есть текст ошибки.
            assert check.error is not None
            assert check.error.strip()
            assert not check.ok
        else:
            assert 100 <= check.status_code <= 599
            assert check.error is None


def test_check_times_are_unique_and_increasing_per_monitor(data: SeedData) -> None:
    by_monitor: defaultdict[int, list[datetime]] = defaultdict(list)
    for check in data.checks:
        by_monitor[check.monitor_ref].append(check.checked_at)

    for times in by_monitor.values():
        assert all(a < b for a, b in pairwise(times))


def test_history_respects_the_requested_length_and_window(data: SeedData) -> None:
    by_monitor: defaultdict[int, list[datetime]] = defaultdict(list)
    for check in data.checks:
        by_monitor[check.monitor_ref].append(check.checked_at)

    for monitor_ref, times in by_monitor.items():
        interval = data.monitors[monitor_ref].interval_seconds
        assert len(times) <= 120
        assert len(times) <= MAX_HISTORY.total_seconds() / interval
        assert NOW - times[0] <= MAX_HISTORY


def test_ok_is_true_only_for_2xx_and_3xx(data: SeedData) -> None:
    for check in data.checks:
        expected = check.status_code is not None and 200 <= check.status_code < 400
        assert check.ok is expected


def test_there_are_both_successes_and_every_kind_of_failure(data: SeedData) -> None:
    assert any(c.ok for c in data.checks)
    assert any(c.status_code is None for c in data.checks)  # ошибка сети
    assert any(
        c.status_code is not None and not c.ok for c in data.checks
    )  # плохой код


# --- incidents --------------------------------------------------------------


def test_incidents_are_failure_series_of_at_least_the_threshold(data: SeedData) -> None:
    assert data.incidents
    for incident in data.incidents:
        assert incident.failed_checks >= INCIDENT_THRESHOLD
        assert incident.last_error.strip()
        assert (
            incident.resolved_at is None or incident.resolved_at > incident.started_at
        )


def test_incident_series_match_the_checks_they_were_built_from(
    data: SeedData,
) -> None:
    for incident in data.incidents:
        opened = data.checks[incident.opened_by_check_ref]
        assert opened.monitor_ref == incident.monitor_ref  # проверка своего монитора
        assert not opened.ok

        failing = [
            c
            for c in data.checks
            if c.monitor_ref == incident.monitor_ref
            and c.checked_at >= incident.started_at
            and (incident.resolved_at is None or c.checked_at < incident.resolved_at)
        ]
        assert len(failing) == incident.failed_checks
        assert all(not c.ok for c in failing)
        # Открывает инцидент проверка, на которой набрался порог.
        assert opened is failing[INCIDENT_THRESHOLD - 1]

        if incident.resolved_by_check_ref is None:
            assert incident.resolved_at is None
        else:
            closed = data.checks[incident.resolved_by_check_ref]
            assert closed.monitor_ref == incident.monitor_ref
            assert closed.ok
            assert closed.checked_at == incident.resolved_at


def test_incidents_of_a_monitor_never_overlap_and_only_the_last_is_open(
    data: SeedData,
) -> None:
    by_monitor: defaultdict[int, list[IncidentRow]] = defaultdict(list)
    for incident in data.incidents:
        by_monitor[incident.monitor_ref].append(incident)

    for incidents in by_monitor.values():
        incidents.sort(key=lambda i: i.started_at)
        for earlier, later in pairwise(incidents):
            assert earlier.resolved_at is not None
            assert earlier.resolved_at <= later.started_at
        assert all(i.resolved_at is not None for i in incidents[:-1])


def test_some_incidents_are_open_and_some_resolved(data: SeedData) -> None:
    states = {i.resolved_at is None for i in data.incidents}

    assert states == {True, False}


# --- audit_log --------------------------------------------------------------


def test_audit_log_follows_the_schema_rules(data: SeedData) -> None:
    allowed = {"user", "monitor", "channel", "check", "incident", "session"}
    sizes = {
        "user": len(data.users),
        "monitor": len(data.monitors),
        "channel": len(data.channels),
        "incident": len(data.incidents),
    }
    request_ids = [e.request_id for e in data.audit_log]

    assert len(set(request_ids)) == len(request_ids)
    for event in data.audit_log:
        assert ACTION.match(event.action)
        assert event.entity_type in allowed
        assert event.occurred_at <= NOW
        assert event.request_id.strip()
        ipaddress.ip_address(event.ip)
        if event.actor_ref is not None:
            assert 0 <= event.actor_ref < len(data.users)
        if event.entity_ref is not None:
            assert 0 <= event.entity_ref < sizes[event.entity_type]


def test_audit_log_is_chronological(data: SeedData) -> None:
    times = [e.occurred_at for e in data.audit_log]

    assert times == sorted(times)


def test_audit_log_is_consistent_with_the_other_tables(data: SeedData) -> None:
    actions: defaultdict[str, int] = defaultdict(int)
    for event in data.audit_log:
        actions[event.action] += 1

    assert actions["user.register"] == len(data.users)
    assert actions["monitor.create"] == len(data.monitors)
    assert actions["channel.create"] == len(data.channels)
    assert actions["incident.open"] == len(data.incidents)
    assert actions["incident.resolve"] == sum(
        i.resolved_at is not None for i in data.incidents
    )


def test_system_events_have_no_actor_and_user_events_have_one(data: SeedData) -> None:
    for event in data.audit_log:
        if event.action.startswith("incident."):
            assert event.actor_ref is None
        if event.action in {"user.register", "user.login", "monitor.create"}:
            assert event.actor_ref is not None


def test_details_are_json_objects_without_secrets(data: SeedData) -> None:
    for event in data.audit_log:
        assert isinstance(event.details, dict)
        assert "password" not in str(event.details).lower()
        assert HASH not in str(event.details)


def test_audit_is_after_what_it_describes(data: SeedData) -> None:
    for event in data.audit_log:
        if event.action == "monitor.create" and event.entity_ref is not None:
            assert event.occurred_at == data.monitors[event.entity_ref].created_at
        if event.action == "user.register" and event.entity_ref is not None:
            assert event.occurred_at == data.users[event.entity_ref].created_at


def test_window_and_session_constants_are_sane() -> None:
    assert timedelta(days=1) <= MAX_HISTORY
    assert timedelta(days=7) == SESSION_TTL


def test_any_timeout_fits_any_interval() -> None:
    # Генератор берёт таймаут и интервал независимо: это допустимо, пока так.
    assert max(TIMEOUTS) < min(INTERVALS)
    assert min(INTERVALS) >= 30
    assert max(INTERVALS) <= 86400


def test_incident_threshold_is_three_failures_in_a_row(data: SeedData) -> None:
    # Значение зафиксировано числом, а не константой: изменить порог осознанно.
    assert INCIDENT_THRESHOLD == 3
    assert min(i.failed_checks for i in data.incidents) >= 3


def test_long_histories_are_capped_by_the_window() -> None:
    big = make(checks_per_monitor=5000)
    by_monitor: defaultdict[int, list[datetime]] = defaultdict(list)
    for check in big.checks:
        by_monitor[check.monitor_ref].append(check.checked_at)

    for monitor_ref, times in by_monitor.items():
        interval = big.monitors[monitor_ref].interval_seconds
        window = int(MAX_HISTORY.total_seconds() // interval)
        assert len(times) == min(5000, window)
        assert NOW - times[0] <= MAX_HISTORY


def test_a_share_of_monitors_is_down_right_now(data: SeedData) -> None:
    open_incidents = sum(i.resolved_at is None for i in data.incidents)

    # Для демонстрации нужны открытые инциденты: часть мониторов «лежит» сейчас.
    assert open_incidents >= 5
