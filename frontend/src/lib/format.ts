/** Интервал проверки из секунд в короткую запись: 30 с, 5 мин, 2 ч. */
export function formatInterval(seconds: number): string {
  if (seconds % 3600 === 0) return `${seconds / 3600} ч`;
  if (seconds % 60 === 0) return `${seconds / 60} мин`;
  return `${seconds} с`;
}

const ROLE_LABELS: Record<string, string> = {
  admin: "администратор",
  manager: "менеджер",
  viewer: "наблюдатель",
};

/** Роль для людей. Неизвестную (новую на бэкенде) показываем как есть. */
export function formatRole(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

/** Задержка проверки: до секунды в миллисекундах, дольше в секундах. */
export function formatLatency(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} мс`;
  return `${(ms / 1000).toFixed(1)} с`;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * Разбор времени из API. PostgreSQL отдаёт микросекунды («.084049Z»), а стандарт
 * ISO для `Date` гарантирует только миллисекунды: Chrome дробную часть длиннее трёх
 * цифр прощает, другие движки нет и дают Invalid Date. Обрезаем до миллисекунд сами.
 */
export function parseApiTime(iso: string): Date {
  return new Date(iso.replace(/(\.\d{3})\d+/, "$1"));
}

/**
 * Сколько прошло с момента `iso` до `now`: «5 мин назад». Время из будущего (часы
 * сервера и браузера расходятся) показываем как «только что», а не отрицательным.
 * Если время не разобрать, показываем прочерк: «NaN дн. назад» хуже, чем ничего.
 */
export function formatAgo(iso: string, now: Date): string {
  const elapsed = now.getTime() - parseApiTime(iso).getTime();
  if (Number.isNaN(elapsed)) return "—";
  if (elapsed < MINUTE) return "только что";
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)} мин назад`;
  if (elapsed < DAY) return `${Math.floor(elapsed / HOUR)} ч назад`;
  return `${Math.floor(elapsed / DAY)} дн. назад`;
}
