"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, auth, sessionStore } from "@/api/client";
import type { components } from "@/api/schema";
import { API_BASE_URL } from "@/lib/config";
import { connectTaskStream, type StreamStatus } from "@/lib/task-stream";
import {
  MAX_ATTEMPTS,
  STATUS_LABEL,
  STATUS_TONE,
  appendEntry,
  buildLanes,
  describeEvent,
  formatSeconds,
  offsetSeconds,
  retryProgress,
  retryRemaining,
  type Lane,
  type TaskEntry,
} from "@/lib/tasks";
import { useSession } from "@/lib/use-session";

type Check = components["schemas"]["CheckRead"];
type MonitorItem = components["schemas"]["MonitorPage"]["items"][number];

const LANES_SHOWN = 12;

type RunResult =
  { status: "ok"; check: Check } | { status: "failed"; message: string };

const CONNECTION: Record<StreamStatus, { text: string; tone: string }> = {
  connecting: { text: "подключаемся…", tone: "tone-neutral" },
  open: { text: "на связи", tone: "tone-ok" },
  reconnecting: {
    text: "связь потеряна, переподключаемся…",
    tone: "tone-warn",
  },
  forbidden: {
    text: "журнал доступен менеджеру и администратору",
    tone: "tone-bad",
  },
  "signed-out": { text: "сессия закончилась", tone: "tone-bad" },
};

/** Текущее время для обратного отсчёта: тикает, только пока `active`. */
function useNow(active: boolean): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(new Date()), 100);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

export default function TasksPage() {
  const router = useRouter();
  const session = useSession();
  const [entries, setEntries] = useState<TaskEntry[]>([]);
  const [connection, setConnection] = useState<StreamStatus>("connecting");
  const [monitors, setMonitors] = useState<MonitorItem[]>([]);
  const [monitorId, setMonitorId] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);

  useEffect(() => {
    void auth.restore();
  }, []);

  useEffect(() => {
    if (session.status === "anonymous") router.replace("/login");
  }, [session.status, router]);

  const authenticated = session.status === "authenticated";

  useEffect(() => {
    if (!authenticated) return;
    let cancelled = false;
    api.GET("/monitors", { params: { query: { limit: 50 } } }).then(
      ({ data }) => {
        if (cancelled || !data) return;
        setMonitors(data.items);
        setMonitorId((current) => current ?? data.items[0]?.id ?? null);
      },
      () => {
        // Без списка мониторов страница остаётся рабочей: журнал виден.
      },
    );
    return () => {
      cancelled = true;
    };
  }, [authenticated]);

  // Поток событий (SSE): сервер сам присылает каждое изменение задач, опрашивать
  // его не нужно. Подключение держит и переподключает connectTaskStream; при
  // размонтировании (и при двойном запуске эффекта в dev-режиме) оно закрывается.
  useEffect(() => {
    if (!authenticated) return;
    return connectTaskStream({
      url: `${API_BASE_URL}/tasks/stream`,
      getToken: sessionStore.getAccessToken,
      refresh: auth.refresh,
      onEntry: (entry) => setEntries((prev) => appendEntry(prev, entry)),
      onStatus: setConnection,
    });
  }, [authenticated]);

  async function runCheck() {
    if (monitorId === null) return;
    setRunning(true);
    setResult(null);
    try {
      const { data, response } = await api.POST(
        "/monitors/{monitor_id}/checks",
        {
          params: { path: { monitor_id: monitorId } },
        },
      );
      if (data) {
        setResult({ status: "ok", check: data });
      } else {
        setResult({
          status: "failed",
          message:
            response.status === 403
              ? "Запускать проверки может менеджер или администратор"
              : "Не удалось запустить проверку",
        });
      }
    } catch {
      setResult({ status: "failed", message: "Нет связи с сервером" });
    } finally {
      setRunning(false);
    }
  }

  if (!authenticated) {
    return (
      <main>
        <p className="muted">Загрузка…</p>
      </main>
    );
  }

  // buildLanes ждёт события новыми первыми, а в состоянии они лежат от старых к новым.
  const lanes = buildLanes([...entries].reverse().map((e) => e.event));

  return (
    <main className="wide">
      <div className="header">
        <h1>Фоновые задачи</h1>
        <div className="account">
          <Link href="/monitors">К мониторам</Link>
        </div>
      </div>
      <p className="muted lead">
        Проверка не прошла: API кладёт в очередь Celery задачу «уведомить о
        сбое» и сразу отвечает. Воркер выполняет её отдельно; если вебхук
        отвечает ошибкой, повторяет с растущей паузой (до {MAX_ATTEMPTS} попыток
        всего). События приходят сами, по SSE: страница ничего не опрашивает.
      </p>

      <div className="controls">
        <label className="field hold">
          Монитор
          <select
            value={monitorId ?? ""}
            disabled={running || monitors.length === 0}
            onChange={(e) => setMonitorId(Number(e.target.value))}
          >
            {monitors.length === 0 && <option value="">Мониторов нет</option>}
            {monitors.map((m) => (
              <option key={m.id} value={m.id}>
                {m.url}
              </option>
            ))}
          </select>
        </label>
        <div className="buttons">
          <button
            disabled={running || monitorId === null}
            onClick={() => void runCheck()}
          >
            {running ? "Проверяем…" : "Запустить проверку"}
          </button>
        </div>
      </div>
      <RunSummary result={result} />

      <ConnectionBadge status={connection} />
      {connection === "open" && lanes.length === 0 && (
        <p className="muted">
          Задач пока не было. Запустите проверку монитора, который не отвечает
          (или отвечает 5xx): успешная проверка уведомления не создаёт.
        </p>
      )}
      <ol className="lanes">
        {lanes.slice(0, LANES_SHOWN).map((lane) => (
          <LaneCard key={lane.checkId} lane={lane} />
        ))}
      </ol>
      {lanes.length > LANES_SHOWN && (
        <p className="muted">
          Показаны {LANES_SHOWN} последних из {lanes.length}
        </p>
      )}
      <Legend />
    </main>
  );
}

function ConnectionBadge({ status }: { status: StreamStatus }) {
  const { text, tone } = CONNECTION[status];
  return (
    <p className="connection" role="status" aria-live="polite">
      <span className={`dot ${tone} ${status === "open" ? "live" : ""}`} />
      {text}
    </p>
  );
}

function RunSummary({ result }: { result: RunResult | null }) {
  if (!result) return null;
  if (result.status === "failed") {
    return (
      <p className="error" role="alert">
        {result.message}
      </p>
    );
  }
  const { check } = result;
  return (
    <p className="run-summary" role="status">
      Проверка #{check.id}:{" "}
      {check.ok ? (
        <span className="status ok">✓ успешна, уведомления не будет</span>
      ) : (
        <span className="status bad">
          ✕ {check.status_code ?? check.error ?? "нет ответа"}, уведомление в
          очереди
        </span>
      )}
    </p>
  );
}

function LaneCard({ lane }: { lane: Lane }) {
  const waiting = lane.status === "waiting";
  const now = useNow(waiting);
  const remaining = retryRemaining(lane, now);
  const progress = retryProgress(lane, now);
  const reason = [...lane.events].reverse().find((e) => e.detail)?.detail;
  const attempts = Math.max(0, ...lane.events.map((e) => e.attempt ?? 0));

  return (
    <li className="lane">
      <div className="lane-head">
        <strong>Проверка #{lane.checkId}</strong>
        <span className={`pill tone-${STATUS_TONE[lane.status]}`}>
          {STATUS_LABEL[lane.status]}
        </span>
        {attempts > 0 && (
          <span className="muted">
            попыток: {attempts} из {MAX_ATTEMPTS}
          </span>
        )}
      </div>
      <ol className="steps">
        {lane.events.map((event, index) => {
          const step = describeEvent(event);
          return (
            <li key={`${event.state}-${event.at}-${index}`} className="step">
              <span className={`pill tone-${step.tone}`}>{step.label}</span>
              <small className="muted">
                +{formatSeconds(offsetSeconds(lane, event))}
              </small>
            </li>
          );
        })}
      </ol>
      {waiting && remaining !== null && progress !== null && (
        <div className="countdown">
          <div
            className="countdown-bar"
            role="progressbar"
            aria-label="Ожидание повтора"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress * 100)}
          >
            <span style={{ width: `${progress * 100}%` }} />
          </div>
          <small className="muted">
            {remaining > 0
              ? `следующая попытка через ${formatSeconds(remaining)}`
              : "ждём воркер"}
          </small>
        </div>
      )}
      {reason && <p className="lane-reason muted">Причина: {reason}</p>}
    </li>
  );
}

function Legend() {
  return (
    <ul className="legend">
      <li>
        <span className="swatch tone-neutral" />в очереди: лежит в Redis, воркер
        ещё не взял
      </li>
      <li>
        <span className="swatch tone-active" />
        попытка: воркер выполняет
      </li>
      <li>
        <span className="swatch tone-warn" />
        повтор: вебхук ответил ошибкой, ждём
      </li>
      <li>
        <span className="swatch tone-ok" />
        отправлено
      </li>
      <li>
        <span className="swatch tone-bad" />
        провал: попытки кончились
      </li>
    </ul>
  );
}
