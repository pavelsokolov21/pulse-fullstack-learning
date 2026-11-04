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
