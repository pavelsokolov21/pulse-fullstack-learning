"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, auth } from "@/api/client";
import type { components } from "@/api/schema";
import {
  formatAgo,
  formatInterval,
  formatLatency,
  formatRole,
} from "@/lib/format";
import {
  PAGE_SIZE,
  clampPage,
  offsetFor,
  pageCount,
  visibleRange,
} from "@/lib/pagination";
import { useSession } from "@/lib/use-session";

type MonitorPage = components["schemas"]["MonitorPage"];
type User = components["schemas"]["UserRead"];
type LastCheck = MonitorPage["items"][number]["last_check"];

// `forPage`: для какой страницы пришёл ответ. Пока он не совпал с выбранной,
// показываем прежние строки (приглушённо), а не мигаем «Загрузка…».
type Loaded =
  | { status: "loading" }
  | { status: "ready"; forPage: number; data: MonitorPage; at: number }
  | { status: "failed"; forPage: number };

export default function MonitorsPage() {
  const router = useRouter();
  const session = useSession();
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [page, setPage] = useState(1);
  const [me, setMe] = useState<User | null>(null);

  // Токен живёт только в памяти, поэтому страница защищена на клиенте:
  // сервер Next.js его не видит (refresh-cookie не уходит дальше /auth).
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
    api.GET("/auth/me").then(
      ({ data }) => {
        if (!cancelled && data) setMe(data);
      },
      () => {
        // Без данных о пользователе страница остаётся рабочей.
      },
    );
    return () => {
      cancelled = true;
    };
  }, [authenticated]);

  useEffect(() => {
    if (!authenticated) return;
    let cancelled = false;
    // Истёкший access-токен обновит middleware клиента, здесь об этом знать не надо.
    api
      .GET("/monitors", {
        params: {
          query: { limit: PAGE_SIZE, offset: offsetFor(page, PAGE_SIZE) },
        },
      })
      .then(
        ({ data }) => {
          if (cancelled) return;
          if (!data) {
            setLoaded({ status: "failed", forPage: page });
            return;
          }
          // Записи могли исчезнуть (удалили последнюю на этой странице): идём на
          // ближайшую существующую, а не показываем пустую таблицу.
          const existing = clampPage(page, data.total, PAGE_SIZE);
          if (existing !== page) setPage(existing);
          else setLoaded({ status: "ready", forPage: page, data, at: Date.now() });
        },
        () => {
          if (!cancelled) setLoaded({ status: "failed", forPage: page });
        },
      );
    return () => {
      cancelled = true;
    };
  }, [authenticated, page]);

  async function onLogout() {
    await auth.logout();
    router.replace("/login");
  }

  if (!authenticated) {
    return (
      <main>
        <p className="muted">Загрузка…</p>
      </main>
    );
  }

  return (
    <main className="wide">
      <div className="header">
        <h1>Мониторы</h1>
        <div className="account">
          <Link href="/concurrency">Конкурентность</Link>
          {me && (
            <span className="user" title={`id ${me.id}`}>
              {me.email} <span className="badge">{formatRole(me.role)}</span>
            </span>
          )}
          <button className="secondary" onClick={onLogout}>
            Выйти
          </button>
        </div>
      </div>
      {loaded.status === "loading" && <p className="muted">Загрузка…</p>}
      {loaded.status === "failed" && (
        <p className="error" role="alert">
          Не удалось загрузить список
        </p>
      )}
      {loaded.status === "ready" && (
        <MonitorTable
          data={loaded.data}
          page={loaded.forPage}
          now={new Date(loaded.at)}
          busy={loaded.forPage !== page}
          currentUserId={me?.id}
          onPage={setPage}
        />
      )}
    </main>
  );
}

function MonitorTable({
  data,
  page,
  now,
  busy,
  currentUserId,
  onPage,
}: {
  data: MonitorPage;
  page: number;
  now: Date;
  busy: boolean;
  currentUserId: number | undefined;
  onPage: (page: number) => void;
}) {
  if (data.total === 0) return <p className="muted">Мониторов пока нет</p>;

  const pages = pageCount(data.total, PAGE_SIZE);
  const range = visibleRange(page, data.items.length, PAGE_SIZE);

  return (
    <>
      <table className="table" aria-busy={busy}>
        <thead>
          <tr>
            <th>URL</th>
            <th>Владелец</th>
            <th>Интервал</th>
            <th>Таймаут</th>
            <th>Последняя проверка</th>
          </tr>
        </thead>
        <tbody className={busy ? "busy" : undefined}>
          {data.items.map((monitor) => (
            <tr key={monitor.id}>
              <td className="url">{monitor.url}</td>
              <td>
                {monitor.owner_email}
                {monitor.owner_id === currentUserId && (
                  <span className="muted"> (вы)</span>
                )}
              </td>
              <td>{formatInterval(monitor.interval)}</td>
              <td>{monitor.timeout} с</td>
              <td>
                <LastCheckCell check={monitor.last_check} now={now} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <nav className="pager" aria-label="Страницы">
        <span className="muted">
          {range && `${range.from}–${range.to} из ${data.total}`}
        </span>
        <div className="pager-controls">
          <button
            className="secondary"
            disabled={busy || page <= 1}
            onClick={() => onPage(page - 1)}
          >
            Назад
          </button>
          <span>
            Страница {page} из {pages}
          </span>
          <button
            className="secondary"
            disabled={busy || page >= pages}
            onClick={() => onPage(page + 1)}
          >
            Вперёд
          </button>
        </div>
      </nav>
    </>
  );
}

function LastCheckCell({ check, now }: { check: LastCheck; now: Date }) {
  if (!check) return <span className="muted">Не проверялся</span>;

  // Успех или нет решает база (`ok`); код ответа лишь подпись. Без ответа вовсе
  // (сеть, таймаут) кода нет, причина лежит в `error`.
  const label = check.status_code ?? "нет ответа";
  return (
    <span className="last-check">
      <span
        className={check.ok ? "status ok" : "status bad"}
        title={check.error ?? `HTTP ${check.status_code}`}
      >
        {check.ok ? "✓" : "✕"} {label}
      </span>
      <span className="muted">
        {formatLatency(check.latency_ms)} ·{" "}
        <time dateTime={check.checked_at} title={check.checked_at}>
          {formatAgo(check.checked_at, now)}
        </time>
      </span>
    </span>
  );
}
