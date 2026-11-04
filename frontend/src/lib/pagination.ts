/** Размер страницы таблицы. Серверный максимум 100, см. `MAX_PAGE_SIZE` на бэкенде. */
export const PAGE_SIZE = 10;

/** Сколько всего страниц. Пустой список это одна (пустая) страница, а не ноль. */
export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

/** Смещение для API: страницы нумеруются с 1. */
export function offsetFor(page: number, pageSize: number): number {
  return (page - 1) * pageSize;
}

/**
 * Ближайшая существующая страница. Нужна, когда данные изменились под ногами:
 * на последней странице удалили последнюю запись, и страницы больше нет.
 */
export function clampPage(page: number, total: number, pageSize: number): number {
  return Math.min(Math.max(1, page), pageCount(total, pageSize));
}

/** Диапазон показанных записей для подписи «11–20 из 47»; `null`, если их нет. */
export function visibleRange(
  page: number,
  count: number,
  pageSize: number,
): { from: number; to: number } | null {
  if (count === 0) return null;
  const from = offsetFor(page, pageSize) + 1;
  return { from, to: from + count - 1 };
}
