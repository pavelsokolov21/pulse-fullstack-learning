import type { Middleware } from "openapi-fetch";

/** Код 401 для access-токена с истёкшим сроком (см. TokenExpiredError). */
export const TOKEN_EXPIRED = "token_expired";

export interface AuthMiddlewareDeps {
  getAccessToken: () => string | null;
  /** Обновляет сессию; возвращает новый access-токен или `null`, если не вышло. */
  refresh: () => Promise<string | null>;
}

/**
 * Подставляет access-токен и лечит истёкший: на 401 с кодом `token_expired`
 * один раз обновляет сессию и повторяет тот же запрос.
 *
 * Аналог axios-интерцептора ответа. Повтор идёт напрямую через `fetch`, мимо
 * middleware, поэтому зациклиться он не может: второй 401 вернётся как есть.
 * Другие 401 (`invalid_token`, `invalid_credentials`) не обновляются: свежий
 * токен им не поможет.
 */
export function createAuthMiddleware({
  getAccessToken,
  refresh,
}: AuthMiddlewareDeps): Middleware {
  // Тело запроса читается один раз, поэтому для повтора нужна копия, снятая
  // до отправки. Ключ: id запроса, который выдаёт openapi-fetch.
  const originals = new Map<string, Request>();

  return {
    onRequest({ request, id }) {
      originals.set(id, request.clone());
      const token = getAccessToken();
      if (token) {
        request.headers.set("Authorization", `Bearer ${token}`);
      }
      return request;
    },

    async onResponse({ request, response, id, options }) {
      const original = originals.get(id);
      originals.delete(id);
      if (!original || response.status !== 401) return;
      if (!(await hasCode(response, TOKEN_EXPIRED))) return;

      // Токен мог обновиться, пока этот запрос был в пути (параллельные запросы
      // получают 401 не одновременно). Тогда обновлять второй раз незачем.
      const current = getAccessToken();
      const alreadyRenewed =
        current !== null &&
        request.headers.get("Authorization") !== `Bearer ${current}`;
      const token = alreadyRenewed ? current : await refresh();
      if (!token) return; // сессии нет: отдаём исходный 401

      original.headers.set("Authorization", `Bearer ${token}`);
      return options.fetch(original);
    },

    onError({ id }) {
      originals.delete(id);
    },
  };
}

async function hasCode(response: Response, code: string): Promise<boolean> {
  // clone(): тело читается здесь, а потом его должен прочитать вызывающий код.
  const body: unknown = await response
    .clone()
    .json()
    .catch(() => null);
  return (
    typeof body === "object" &&
    body !== null &&
    "code" in body &&
    body.code === code
  );
}
