import type { components } from "@/api/schema";

export type Strategy = components["schemas"]["Strategy"];
export type TraceEvent = components["schemas"]["TraceEventRead"];
export type ConcurrencyRun = components["schemas"]["ConcurrencyRunRead"];

export const STRATEGY_IDS: Strategy[] = ["unsafe", "lock", "version"];

export const STRATEGY_INFO: Record<
  Strategy,
  { title: string; summary: string; how: string }
> = {
  unsafe: {
    title: "Без защиты",
    summary: "Прочитать, прибавить, записать.",
    how: "Оба запроса читают одно и то же число, оба прибавляют 1 и записывают одно и то же значение. Второй коммит затирает первый: один прирост потерян (lost update).",
  },
  lock: {
    title: "Блокировка строки",
    summary: "SELECT … FOR UPDATE.",
    how: "Чтение берёт блокировку строки до коммита. Второй запрос стоит в очереди на чтении и видит уже новое значение. Ничего не теряется, но второй ждёт первого (оранжевый участок).",
  },
  version: {
    title: "Версия строки",
    summary: "UPDATE … WHERE version = :прочитанная.",
    how: "Блокировок нет, оба читают и пишут. Запись проигравшего отклоняется (красный ✕), потому что версия уже изменилась, и вызывающий код повторяет всю попытку заново. Теряется работа, а не данные.",
  },
};

export type SegmentKind = "wait" | "hold" | "commit";

export type Segment = {
  kind: SegmentKind;
  attempt: number;
  fromMs: number;
  toMs: number;
  /** Подпись внутри полосы; пустая, если места на ней всё равно нет. */
  label: string;
  /** Подсказка при наведении. */
  title: string;
};

export type Lane = {
  call: number;
  segments: Segment[];
  conflicts: { attempt: number; atMs: number }[];
};

/**
 * События одного прогона -> полосы по вызовам (по одной на «запрос»).
 *
 * Участки: `wait` от запроса блокировки до чтения, `hold` от чтения до записи
 * (или до отклонённой записи), `commit` от записи до коммита.
 */
export function buildLanes(events: TraceEvent[]): Lane[] {
  const calls = [...new Set(events.map((e) => e.call))].sort((a, b) => a - b);
  return calls.map((call) => {
    const lane: Lane = { call, segments: [], conflicts: [] };
    let lockAt: number | null = null;
    let readAt: number | null = null;
    let readValue: number | null = null;
    let writeAt: number | null = null;
    const mine = events
      .filter((e) => e.call === call)
      .sort((a, b) => a.at_ms - b.at_ms);

    for (const e of mine) {
      switch (e.kind) {
        case "start":
          lockAt = readAt = writeAt = null;
          readValue = null;
          break;
        case "lock_requested":
          lockAt = e.at_ms;
          break;
        case "read":
          if (lockAt !== null) {
            lane.segments.push({
              kind: "wait",
              attempt: e.attempt,
              fromMs: lockAt,
              toMs: e.at_ms,
              label: "ждёт блокировку",
              title: `Ждал блокировку строки ${fmtMs(e.at_ms - lockAt)}`,
            });
            lockAt = null;
          }
          readAt = e.at_ms;
          readValue = e.value;
          break;
        case "write":
          if (readAt !== null) {
            lane.segments.push({
              kind: "hold",
              attempt: e.attempt,
              fromMs: readAt,
              toMs: e.at_ms,
              label: `прочитал ${readValue} → записал ${e.value}`,
              title: `Держал прочитанное ${fmtMs(e.at_ms - readAt)}: прочитал ${readValue}, записал ${e.value}`,
            });
          }
          writeAt = e.at_ms;
          break;
        case "conflict":
          if (readAt !== null) {
            lane.segments.push({
              kind: "hold",
              attempt: e.attempt,
              fromMs: readAt,
              toMs: e.at_ms,
              label: `прочитал ${readValue} → отклонено`,
              title: `Прочитал ${readValue}, но версия строки уже изменилась: запись отклонена`,
            });
          }
          lane.conflicts.push({ attempt: e.attempt, atMs: e.at_ms });
          break;
        case "commit":
          if (writeAt !== null) {
            lane.segments.push({
              kind: "commit",
              attempt: e.attempt,
              fromMs: writeAt,
              toMs: e.at_ms,
              label: "",
              title: "Коммит: блокировка снята, изменение видно другим",
            });
          }
          break;
      }
    }
    return lane;
  });
}

/** Правая граница шкалы: конец прогона или последнее событие, что позже. */
export function timelineEnd(events: TraceEvent[], elapsedMs: number): number {
  return Math.max(elapsedMs, ...events.map((e) => e.at_ms), 1);
}

const TICK_STEPS = [50, 100, 200, 250, 500, 1000, 2000];
const MAX_TICKS = 8;

/** Отметки оси: «круглый» шаг, не больше `MAX_TICKS` штук. */
export function ticks(endMs: number): number[] {
  const step =
    TICK_STEPS.find((s) => endMs / s <= MAX_TICKS) ??
    TICK_STEPS[TICK_STEPS.length - 1];
  const result: number[] = [];
  for (let t = 0; t <= endMs; t += step) result.push(t);
  return result;
}

/** Положение на шкале в процентах, ограниченное 0..100. */
export function percent(ms: number, endMs: number): number {
  if (endMs <= 0) return 0;
  return Math.min(100, Math.max(0, (ms / endMs) * 100));
}

export function fmtMs(ms: number): string {
  return `${Math.round(ms)} мс`;
}

/** Событие для журнала, человеческими словами. */
export function describeEvent(e: TraceEvent): string {
  switch (e.kind) {
    case "start":
      return e.attempt > 1 ? `попытка ${e.attempt}: начало` : "начало";
    case "lock_requested":
      return "запросил блокировку строки (если занята, ждёт)";
    case "read":
      return `прочитал ${e.value}`;
    case "write":
      return `записал ${e.value} (ещё не виден другим)`;
    case "commit":
      return "коммит";
    case "conflict":
      return "конфликт версии: запись отклонена";
  }
}

export function verdict(run: ConcurrencyRun): {
  tone: "ok" | "bad";
  text: string;
} {
  if (run.lost_update) {
    return {
      tone: "bad",
      text: `Потеряно обновление: ожидали ${run.expected}, в базе ${run.final}`,
    };
  }
  return {
    tone: "ok",
    text: `Верно: два прироста на месте, в базе ${run.final}`,
  };
}
