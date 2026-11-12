"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, auth } from "@/api/client";
import type { components } from "@/api/schema";
import {
  MAX_ATTEMPTS,
  STATUS_LABEL,
  STATUS_TONE,
  buildLanes,
  describeEvent,
  formatSeconds,
  offsetSeconds,
  retryProgress,
  retryRemaining,
  type Lane,
  type TaskEvent,
} from "@/lib/tasks";
import { useSession } from "@/lib/use-session";

type Check = components["schemas"]["CheckRead"];
type MonitorItem = components["schemas"]["MonitorPage"]["items"][number];

const POLL_MS = 1000;
const EVENTS_LIMIT = 200;
const LANES_SHOWN = 12;

type Feed =
  | { status: "loading" }
  | { status: "ready"; events: TaskEvent[] }
  | { status: "failed"; code: number };

type RunResult =
  | { status: "ok"; check: Check }
  | { status: "failed"; message: string };

function feedError(code: number): string {
  if (code === 403) return "Журнал задач доступен менеджеру и администратору";
  if (code === 0) return "Нет связи с сервером";
  return "Не удалось загрузить журнал задач";
}

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
  const [feed, setFeed] = useState<Feed>({ status: "loading" });
  const [monitors, setMonitors] = useState<MonitorItem[]>([]);
  const [monitorId, setMonitorId] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  // Меняется после запуска проверки: цикл опроса начинается заново и сразу
  // забирает новые события, а не ждёт очередной секунды.
  const [reload, setReload] = useState(0);

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

  // Опрос раз в секунду. Следующий запрос планируется после ответа предыдущего, а не
  // по таймеру: медленный ответ не наложится на следующий. Скрытая вкладка не опрашивает.
  useEffect(() => {
    if (!authenticated) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function tick() {
      let stop = false;
      if (!document.hidden) {
        try {
          const { data, response } = await api.GET("/tasks/events", {
            params: { query: { limit: EVENTS_LIMIT } },
          });
          if (cancelled) return;
          if (data) setFeed({ status: "ready", events: data });
          else {
            setFeed({ status: "failed", code: response.status });
            // Прав нет: повторять бессмысленно, пока человек не сменит роль.
            stop = response.status === 403;
          }
        } catch {
          if (cancelled) return;
          setFeed({ status: "failed", code: 0 });
        }
      }
      if (!cancelled && !stop) timer = setTimeout(() => void tick(), POLL_MS);
    }

    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [authenticated, reload]);

  async function runCheck() {
    if (monitorId === null) return;
    setRunning(true);
    setResult(null);
    try {
      const { data, response } = await api.POST("/monitors/{monitor_id}/checks", {
        params: { path: { monitor_id: monitorId } },
      });
      if (data) {
        setResult({ status: "ok", check: data });
        setReload((n) => n + 1);
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

  const lanes = feed.status === "ready" ? buildLanes(feed.events) : [];

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
        всего). Страница раз в секунду читает журнал событий.
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

      {feed.status === "loading" && <p className="muted">Загрузка…</p>}
      {feed.status === "failed" && (
        <p className="error" role="alert">
          {feedError(feed.code)}
        </p>
      )}
      {feed.status === "ready" && lanes.length === 0 && (
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
