import type { components } from "@/api/schema";

export type ReportWindow = components["schemas"]["ReportWindow"];
export type MonitorReport = components["schemas"]["MonitorReportRead"];

export const REPORT_WINDOWS: { id: ReportWindow; label: string }[] = [
  { id: "24h", label: "24 часа" },
  { id: "7d", label: "7 дней" },
  { id: "30d", label: "30 дней" },
];

/** Откуда пришёл отчёт: заголовок `X-Cache`. Нет заголовка значит «неизвестно». */
export type CacheStatus = "HIT" | "MISS" | null;

export function parseCacheStatus(header: string | null): CacheStatus {
  return header === "HIT" || header === "MISS" ? header : null;
}

/** Аптайм в процентах; `null` значит, что за окно нет ни одной проверки. */
export function formatUptime(percent: number | null): string {
  if (percent === null) return "—";
  // До 3 знаков, но без хвоста нулей: 100 %, 99.5 %, 66.667 %.
  return `${Number(percent.toFixed(3))} %`;
}

/** Задержка отчёта: `null` значит «ни одного ответа», показываем прочерк. */
export function formatReportLatency(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} мс`;
  return `${(ms / 1000).toFixed(1)} с`;
}

export type UptimeTone = "ok" | "warn" | "bad" | "none";

/** Цвет аптайма: от 99 % всё хорошо, от 95 % внимание, ниже плохо. */
export function uptimeTone(percent: number | null): UptimeTone {
  if (percent === null) return "none";
  if (percent >= 99) return "ok";
  if (percent >= 95) return "warn";
  return "bad";
}

/** Номер монитора из адреса страницы; всё, что не натуральное число, это `null`. */
export function parseMonitorId(raw: string | string[] | undefined): number | null {
  if (typeof raw !== "string" || !/^[1-9]\d*$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : null;
}

export type FetchLogEntry = {
  id: number;
  window: ReportWindow;
  cache: CacheStatus;
  ms: number;
};

const LOG_SIZE = 8;

/** Журнал запросов отчёта: новые сверху, хранится не больше `LOG_SIZE`. */
export function pushLog(
  log: FetchLogEntry[],
  entry: FetchLogEntry,
): FetchLogEntry[] {
  return [entry, ...log].slice(0, LOG_SIZE);
}
