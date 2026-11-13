import type { components } from "@/api/schema";
import { parseApiTime } from "@/lib/format";

export type TaskEvent = components["schemas"]["TaskEventRead"];
export type TaskState = TaskEvent["state"];

/** Событие с номером записи в журнале сервера (`id` из SSE). */
export type TaskEntry = { id: string; event: TaskEvent };

/** Сколько событий держим в памяти страницы; столько же хранит и сервер. */
export const MAX_ENTRIES = 300;

/**
 * Добавляет событие в конец списка (от старых к новым). Повтор того же номера
 * пропускаем: после переподключения или двойного запуска эффекта в dev-режиме React
 * сервер может прислать то, что страница уже видела. Лишнее с начала отбрасываем.
 */
export function appendEntry(
  entries: TaskEntry[],
  entry: TaskEntry,
  max: number = MAX_ENTRIES,
): TaskEntry[] {
  if (entries.some((e) => e.id === entry.id)) return entries;
  const next = [...entries, entry];
  return next.length > max ? next.slice(next.length - max) : next;
}

/** Где задача находится сейчас. Определяется последним событием дорожки. */
export type LaneStatus = "queued" | "running" | "waiting" | "done" | "failed";

export type Tone = "neutral" | "active" | "warn" | "ok" | "bad";

export type Lane = {
  checkId: number;
  /** От старых к новым: так читается история. */
  events: TaskEvent[];
  status: LaneStatus;
};

export const MAX_ATTEMPTS = 6; // первая попытка и 5 повторов, как в воркере

const STATUS_BY_STATE: Record<TaskState, LaneStatus> = {
  queued: "queued",
  started: "running",
  retry: "waiting",
  sent: "done",
  duplicate: "done",
  skipped: "done",
  failed: "failed",
};

export const STATUS_LABEL: Record<LaneStatus, string> = {
  queued: "в очереди",
  running: "выполняется",
  waiting: "ждёт повтора",
  done: "готово",
  failed: "провал",
};

export const STATUS_TONE: Record<LaneStatus, Tone> = {
  queued: "neutral",
  running: "active",
  waiting: "warn",
  done: "ok",
  failed: "bad",
};

function time(event: TaskEvent): number {
  return parseApiTime(event.at).getTime();
}

/**
 * Собирает события журнала в дорожки: по одной на проверку.
 *
 * Журнал отдаёт события новыми первыми, а читать историю удобнее от старых к новым,
 * поэтому порядок разворачиваем. События с одинаковым временем остаются в порядке
 * записи (сортировка в JS стабильная). Дорожки идут от недавно менявшихся к давним.
 */
export function buildLanes(newestFirst: TaskEvent[]): Lane[] {
  const byCheck = new Map<number, TaskEvent[]>();
  for (const event of [...newestFirst].reverse()) {
    const list = byCheck.get(event.check_id);
    if (list) list.push(event);
    else byCheck.set(event.check_id, [event]);
  }

  const lanes: Lane[] = [];
  for (const [checkId, events] of byCheck) {
    const sorted = [...events].sort((a, b) => time(a) - time(b));
    lanes.push({
      checkId,
      events: sorted,
      status: STATUS_BY_STATE[sorted[sorted.length - 1].state],
    });
  }
  return lanes.sort((a, b) => lastTime(b) - lastTime(a));
}

function lastTime(lane: Lane): number {
  return time(lane.events[lane.events.length - 1]);
}

/** Сколько секунд осталось до следующей попытки; `null`, если повтор не ждём. */
export function retryRemaining(lane: Lane, now: Date): number | null {
  const last = lane.events[lane.events.length - 1];
  if (last.state !== "retry" || last.retry_in === null) return null;
  const due = time(last) + last.retry_in * 1000;
  return Math.max(0, (due - now.getTime()) / 1000);
}

/** Доля пройденной паузы до повтора, от 0 до 1. */
export function retryProgress(lane: Lane, now: Date): number | null {
  const remaining = retryRemaining(lane, now);
  const last = lane.events[lane.events.length - 1];
  if (remaining === null || last.retry_in === null) return null;
  if (last.retry_in <= 0) return 1;
  return Math.min(1, Math.max(0, 1 - remaining / last.retry_in));
}

/** Секунды с первого события дорожки: «+1.9 с» под каждым шагом. */
export function offsetSeconds(lane: Lane, event: TaskEvent): number {
  return (time(event) - time(lane.events[0])) / 1000;
}

export function formatSeconds(seconds: number): string {
  return `${seconds.toFixed(1)} с`;
}

export type Step = { label: string; tone: Tone };

/** Подпись и цвет одного шага на дорожке. */
export function describeEvent(event: TaskEvent): Step {
  switch (event.state) {
    case "queued":
      return { label: "в очереди", tone: "neutral" };
    case "started":
      return { label: `попытка ${event.attempt ?? "?"}`, tone: "active" };
    case "retry":
      return {
        label:
          event.retry_in === null
            ? "повтор"
            : `повтор через ${formatSeconds(event.retry_in)}`,
        tone: "warn",
      };
    case "sent":
      return { label: "отправлено", tone: "ok" };
    case "duplicate":
      return { label: "уже отправляли", tone: "ok" };
    case "skipped":
      return { label: "сообщать не о чем", tone: "neutral" };
    case "failed":
      return { label: "провал", tone: "bad" };
  }
}
