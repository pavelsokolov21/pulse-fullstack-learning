"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, auth } from "@/api/client";
import type { components } from "@/api/schema";
import { formatInterval } from "@/lib/format";
import { useSession } from "@/lib/use-session";

type Monitor = components["schemas"]["MonitorRead"];

type Loaded =
  | { status: "loading" }
  | { status: "ready"; monitors: Monitor[] }
  | { status: "failed" };

export default function MonitorsPage() {
  const router = useRouter();
  const session = useSession();
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });

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
    // Истёкший access-токен обновит middleware клиента, здесь об этом знать не надо.
    api.GET("/monitors").then(
      ({ data }) => {
        if (cancelled) return;
        setLoaded(
          data ? { status: "ready", monitors: data } : { status: "failed" },
        );
      },
      () => {
        if (!cancelled) setLoaded({ status: "failed" });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [authenticated]);

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
    <main>
      <div className="header">
        <h1>Мониторы</h1>
        <button className="secondary" onClick={onLogout}>
          Выйти
        </button>
      </div>
      {loaded.status === "loading" && <p className="muted">Загрузка…</p>}
      {loaded.status === "failed" && (
        <p className="error" role="alert">
          Не удалось загрузить список
        </p>
      )}
      {loaded.status === "ready" && loaded.monitors.length === 0 && (
        <p className="muted">Мониторов пока нет</p>
      )}
      {loaded.status === "ready" && loaded.monitors.length > 0 && (
        <ul className="list">
          {loaded.monitors.map((monitor) => (
            <li key={monitor.id}>
              <span className="url">{monitor.url}</span>
              <span className="muted">
                каждые {formatInterval(monitor.interval)}, таймаут{" "}
                {monitor.timeout} с
              </span>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
