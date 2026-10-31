export type SessionState =
  | { status: "unknown" } // ещё не знаем: идёт восстановление по refresh-cookie
  | { status: "anonymous" }
  | { status: "authenticated"; accessToken: string };

export interface SessionStore {
  getState(): SessionState;
  getAccessToken(): string | null;
  signIn(accessToken: string): void;
  signOut(): void;
  /** Подписка в формате `useSyncExternalStore`; возвращает функцию отписки. */
  subscribe(listener: () => void): () => void;
}

/**
 * Хранилище access-токена в памяти модуля.
 *
 * Не `localStorage`: любой XSS-скрипт прочитал бы токен оттуда. Платим тем,
 * что после перезагрузки страницы токена нет, и сессию восстанавливает вызов
 * `/auth/refresh` (httpOnly-cookie браузер приложит сам).
 */
export function createSessionStore(): SessionStore {
  let state: SessionState = { status: "unknown" };
  const listeners = new Set<() => void>();

  function set(next: SessionState) {
    state = next; // новый объект: так React видит изменение
    listeners.forEach((listener) => listener());
  }

  return {
    getState: () => state,
    getAccessToken: () =>
      state.status === "authenticated" ? state.accessToken : null,
    signIn: (accessToken) => set({ status: "authenticated", accessToken }),
    signOut: () => set({ status: "anonymous" }),
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
