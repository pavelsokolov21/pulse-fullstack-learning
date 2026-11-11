"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { api, auth } from "@/api/client";
import type { components } from "@/api/schema";
import { formatAgo, formatInterval } from "@/lib/format";
import {
  REPORT_WINDOWS,
  formatReportLatency,
  formatUptime,
  parseCacheStatus,
  parseMonitorId,
  pushLog,
  uptimeTone,
  type CacheStatus,
  type FetchLogEntry,
  type MonitorReport,
  type ReportWindow,
} from "@/lib/report";
import { useSession } from "@/lib/use-session";

type Monitor = components["schemas"]["MonitorRead"];

type View =
  | { status: "loading" }
  | { status: "ready"; forWindow: ReportWindow; report: MonitorReport; at: number }
  | { status: "failed"; message: string };

function reportError(status: number): string {
  if (status === 404) return "Монитор не найден";
  return "Не удалось загрузить отчёт";
}

// `useParams` читает адрес страницы, который при сборке неизвестен: Next требует
// границу `Suspense`, иначе страницу нельзя собрать заранее.
export default function MonitorReportPage() {
  return (
    <Suspense
      fallback={
        <main>
          <p className="muted">Загрузка…</p>
        </main>
      }
    >
      <MonitorReport />
    </Suspense>
  );
}

function MonitorReport() {
  const router = useRouter();
  const session = useSession();
  const params = useParams<{ id: string }>();
  const monitorId = parseMonitorId(params.id);

  const [monitor, setMonitor] = useState<Monitor | null>(null);
  const [window, setWindow] = useState<ReportWindow>("7d");
  const [view, setView] = useState<View>({ status: "loading" });
  const [cache, setCache] = useState<CacheStatus>(null);
  const [log, setLog] = useState<FetchLogEntry[]>([]);
  const [reload, setReload] = useState(0);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const logId = useRef(0);

  // Токен живёт в памяти: страница защищена на клиенте (см. страницу мониторов).
  useEffect(() => {
    void auth.restore();
  }, []);

  useEffect(() => {
    if (session.status === "anonymous") router.replace("/login");
  }, [session.status, router]);

  const authenticated = session.status === "authenticated";

  useEffect(() => {
    if (!authenticated || monitorId === null) return;
    let cancelled = false;
    api
      .GET("/monitors/{monitor_id}", { params: { path: { monitor_id: monitorId } } })
      .then(
        ({ data }) => {
          if (!cancelled && data) setMonitor(data);
        },
        () => {
          // Без шапки с адресом отчёт всё равно показывается.
        },
      );
    return () => {
      cancelled = true;
    };
  }, [authenticated, monitorId]);

  useEffect(() => {
    if (!authenticated || monitorId === null) return;
    let cancelled = false;
    const started = performance.now();
    api
      .GET("/monitors/{monitor_id}/report", {
        params: { path: { monitor_id: monitorId }, query: { window } },
      })
      .then(
        ({ data, response }) => {
          if (cancelled) return;
          if (!data) {
            setView({ status: "failed", message: reportError(response.status) });
            return;
          }
          const status = parseCacheStatus(response.headers.get("x-cache"));
          const ms = performance.now() - started;
          setView({ status: "ready", forWindow: window, report: data, at: Date.now() });
          setCache(status);
          setLog((prev) =>
            pushLog(prev, { id: ++logId.current, window, cache: status, ms }),
          );
        },
        () => {
          if (!cancelled) {
            setView({ status: "failed", message: reportError(0) });
          }
        },
      );
    return () => {
      cancelled = true;
    };
  }, [authenticated, monitorId, window, reload]);

  async function runCheck() {
    if (monitorId === null) return;
    setChecking(true);
    setCheckError(null);
    const { response } = await api.POST("/monitors/{monitor_id}/checks", {
      params: { path: { monitor_id: monitorId } },
    });
    setChecking(false);
    if (!response.ok) {
      setCheckError(
        response.status === 403
          ? "Запускать проверки могут менеджер и администратор"
          : "Не удалось выполнить проверку",
      );
      return;
    }
    // Новая проверка сбрасывает кеш на сервере: следующий отчёт будет посчитан заново.
    setReload((n) => n + 1);
  }

  if (monitorId === null) {
    return (
      <main>
        <p className="error" role="alert">
          Некорректный адрес монитора
        </p>
        <Link href="/monitors">К мониторам</Link>
      </main>
    );
  }

  if (!authenticated) {
    return (
      <main>
        <p className="muted">Загрузка…</p>
      </main>
    );
  }

  const busy = view.status === "ready" && view.forWindow !== window;

  return (
    <main className="wide">
      <div className="header">
        <h1>Отчёт монитора</h1>
        <div className="account">
          <Link href="/monitors">К мониторам</Link>
        </div>
      </div>
      {monitor && (
        <p className="muted lead">
          <span className="url">{monitor.url}</span> · проверка каждые{" "}
          {formatInterval(monitor.interval)}
        </p>
      )}

      <div className="controls">
        <div className="segmented" role="radiogroup" aria-label="Окно отчёта">
          {REPORT_WINDOWS.map((w) => (
            <button
              key={w.id}
              type="button"
              role="radio"
              aria-checked={window === w.id}
              className={window === w.id ? "active" : "secondary"}
              onClick={() => setWindow(w.id)}
            >
              {w.label}
            </button>
          ))}
        </div>
        <div className="buttons">
          <button
            className="secondary"
            onClick={() => setReload((n) => n + 1)}
            disabled={view.status === "loading" || busy}
          >
            Обновить
          </button>
          <button onClick={() => void runCheck()} disabled={checking}>
            {checking ? "Проверяем…" : "Запустить проверку"}
          </button>
        </div>
      </div>

      {checkError && (
        <p className="error" role="alert">
          {checkError}
        </p>
      )}
      {view.status === "loading" && <p className="muted">Загрузка…</p>}
      {view.status === "failed" && (
        <p className="error" role="alert">
          {view.message}
        </p>
      )}
      {view.status === "ready" && (
        <ReportView view={view} cache={cache} busy={busy} />
      )}

      {log.length > 0 && <FetchLog log={log} />}
    </main>
  );
}

function ReportView({
  view,
  cache,
  busy,
}: {
  view: Extract<View, { status: "ready" }>;
  cache: CacheStatus;
  busy: boolean;
}) {
  const { report } = view;
  const tone = uptimeTone(report.uptime_percent);

  return (
    <section aria-busy={busy} className={busy ? "busy" : undefined}>
      <div className="report-meta">
        {cache && (
          <span className={`cache cache-${cache.toLowerCase()}`}>
            {cache === "HIT" ? "из кеша" : "посчитано по базе"}
          </span>
        )}
        <span className="muted">
          отчёт сформирован{" "}
          <time dateTime={report.generated_at} title={report.generated_at}>
            {formatAgo(report.generated_at, new Date(view.at))}
          </time>
        </span>
      </div>

      {report.total === 0 ? (
        <p className="muted">
          За это окно проверок нет. Запустите проверку или выберите окно шире.
        </p>
      ) : (
        <dl className="cards">
          <div className={`card tone-${tone}`}>
            <dt>Аптайм</dt>
            <dd>{formatUptime(report.uptime_percent)}</dd>
          </div>
          <div className="card">
            <dt>Проверок</dt>
            <dd>
              {report.ok} <span className="muted">из {report.total} успешных</span>
            </dd>
          </div>
          <div className="card">
            <dt>Средняя задержка</dt>
            <dd>{formatReportLatency(report.avg_latency_ms)}</dd>
          </div>
          <div className="card">
            <dt>p95 задержки</dt>
            <dd>{formatReportLatency(report.p95_latency_ms)}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}

function FetchLog({ log }: { log: FetchLogEntry[] }) {
  return (
    <details className="race-log" open>
      <summary>Запросы отчёта ({log.length})</summary>
      <table className="table">
        <thead>
          <tr>
            <th>Окно</th>
            <th>Ответ</th>
            <th>Время</th>
          </tr>
        </thead>
        <tbody>
          {log.map((entry) => (
            <tr key={entry.id}>
              <td>{entry.window}</td>
              <td>
                {entry.cache ? (
                  <span className={`cache cache-${entry.cache.toLowerCase()}`}>
                    {entry.cache === "HIT" ? "из кеша" : "по базе"}
                  </span>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td>{Math.round(entry.ms)} мс</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
