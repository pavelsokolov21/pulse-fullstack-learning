"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, auth } from "@/api/client";
import { Legend, RaceResult } from "@/components/race-timeline";
import {
  STRATEGY_IDS,
  STRATEGY_INFO,
  type ConcurrencyRun,
  type Strategy,
} from "@/lib/concurrency";
import { useSession } from "@/lib/use-session";

const HOLD_MIN = 100;
const HOLD_MAX = 2000;
const HOLD_STEP = 100;
const HOLD_DEFAULT = 400;

type Runs = Partial<Record<Strategy, ConcurrencyRun>>;

function errorText(status: number): string {
  if (status === 403) return "Нужна роль менеджера или администратора";
  if (status === 404) return "Демо выключено (в prod его нет)";
  return "Не удалось запустить гонку";
}

export default function ConcurrencyPage() {
  const router = useRouter();
  const session = useSession();
  const [strategy, setStrategy] = useState<Strategy>("unsafe");
  const [holdMs, setHoldMs] = useState(HOLD_DEFAULT);
  const [runs, setRuns] = useState<Runs>({});
  const [running, setRunning] = useState<Strategy[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Токен живёт в памяти: страница защищена на клиенте (см. страницу мониторов).
  useEffect(() => {
    void auth.restore();
  }, []);

  useEffect(() => {
    if (session.status === "anonymous") router.replace("/login");
  }, [session.status, router]);

  if (session.status !== "authenticated") {
    return (
      <main>
        <p className="muted">Загрузка…</p>
      </main>
    );
  }

  // Прогоны идут по одному: параллельные запустили бы четыре «запроса» вместо двух
  // на одной базе и исказили бы время.
  async function runStrategies(list: Strategy[]) {
    setError(null);
    setRuns({});
    for (const id of list) {
      setRunning([id]);
      const { data, response } = await api.POST("/demo/concurrency", {
        body: { strategy: id, hold_ms: holdMs },
      });
      if (!data) {
        setError(errorText(response.status));
        break;
      }
      setRuns((prev) => ({ ...prev, [id]: data }));
    }
    setRunning([]);
  }

  const busy = running.length > 0;
  const shown = STRATEGY_IDS.filter((id) => runs[id]);

  return (
    <main className="wide">
      <div className="header">
        <h1>Конкурентность</h1>
        <div className="account">
          <Link href="/monitors">К мониторам</Link>
        </div>
      </div>
      <p className="muted lead">
        Два запроса одновременно увеличивают один счётчик (сейчас 3, ждём 5).
        Это настоящая гонка на вашей базе: на время прогона создаётся временный
        монитор, потом он удаляется.
      </p>

      <fieldset className="strategies" disabled={busy}>
        <legend>Способ защиты</legend>
        {STRATEGY_IDS.map((id) => (
          <label
            key={id}
            className={`strategy ${strategy === id ? "selected" : ""}`}
          >
            <input
              type="radio"
              name="strategy"
              value={id}
              checked={strategy === id}
              onChange={() => setStrategy(id)}
            />
            <span>
              <strong>{STRATEGY_INFO[id].title}</strong>
              <code>{STRATEGY_INFO[id].summary}</code>
            </span>
          </label>
        ))}
      </fieldset>
      <p className="how">{STRATEGY_INFO[strategy].how}</p>

      <div className="controls">
        <label className="field hold">
          Пауза между чтением и записью: {holdMs} мс
          <input
            type="range"
            min={HOLD_MIN}
            max={HOLD_MAX}
            step={HOLD_STEP}
            value={holdMs}
            disabled={busy}
            onChange={(e) => setHoldMs(Number(e.target.value))}
          />
        </label>
        <div className="buttons">
          <button disabled={busy} onClick={() => void runStrategies([strategy])}>
            {busy ? "Идёт гонка…" : "Запустить гонку"}
          </button>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => void runStrategies(STRATEGY_IDS)}
          >
            Сравнить все три
          </button>
        </div>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {shown.length > 0 && <Legend />}
      {shown.map((id) => (
        <RaceResult key={id} run={runs[id] as ConcurrencyRun} />
      ))}
    </main>
  );
}
