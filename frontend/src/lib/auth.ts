import type { Client } from "openapi-fetch";

import type { paths } from "@/api/schema";
import { FORM_CONTENT_TYPE, toFormBody } from "@/lib/form";
import type { SessionStore } from "@/lib/session-store";

export type LoginResult =
  | { ok: true }
  | { ok: false; reason: "invalid_credentials" }
  | { ok: false; reason: "rate_limited"; retryAfter: number | null }
  | { ok: false; reason: "unavailable" };

/**
 * Вход, выход и обновление сессии.
 *
 * Принимает «голый» клиент без auth-middleware: вызов `/auth/refresh` через
 * клиент с middleware мог бы сам вызвать обновление и зациклиться.
 */
export function createAuth(client: Client<paths>, store: SessionStore) {
  let inflight: Promise<string | null> | null = null;
  let restoring: Promise<void> | null = null;

  async function requestRefresh(): Promise<string | null> {
    try {
      const { data, response } = await client.POST("/auth/refresh");
      if (data) {
        store.signIn(data.access_token);
        return data.access_token;
      }
      // 401: refresh-токена нет, он просрочен или отозван. Сессия кончилась.
      // Сбой сети или 5xx сессию не ломают: можно попробовать позже.
      if (response.status === 401) store.signOut();
      return null;
    } catch {
      return null;
    }
  }

  /**
   * Обновляет сессию. Параллельные вызовы делят один запрос: refresh-токен
   * одноразовый, и второй вызов с тем же cookie сервер принял бы за кражу и
   * отозвал бы всю цепочку.
   */
  function refresh(): Promise<string | null> {
    inflight ??= requestRefresh().finally(() => {
      inflight = null;
    });
    return inflight;
  }

  /**
   * Восстанавливает сессию при загрузке страницы (токен в памяти пропал, cookie
   * осталась). Выполняется один раз: React в dev-режиме запускает эффекты дважды.
   */
  function restore(): Promise<void> {
    restoring ??= refresh().then(() => {
      if (store.getState().status === "unknown") store.signOut();
    });
    return restoring;
  }

  async function login(email: string, password: string): Promise<LoginResult> {
    try {
      const { data, response } = await client.POST("/auth/login", {
        // OAuth2 password flow ждёт форму, а не JSON, и поле зовётся username.
        body: { username: email, password, scope: "" },
        bodySerializer: toFormBody,
        headers: { "Content-Type": FORM_CONTENT_TYPE },
      });
      if (data) {
        store.signIn(data.access_token);
        return { ok: true };
      }
      if (response.status === 401) {
        return { ok: false, reason: "invalid_credentials" };
      }
      if (response.status === 429) {
        const seconds = Number(response.headers.get("Retry-After"));
        return {
          ok: false,
          reason: "rate_limited",
          retryAfter: Number.isFinite(seconds) && seconds > 0 ? seconds : null,
        };
      }
      return { ok: false, reason: "unavailable" };
    } catch {
      return { ok: false, reason: "unavailable" };
    }
  }

  async function logout(): Promise<void> {
    try {
      await client.POST("/auth/logout");
    } catch {
      // Сервер недоступен: локально всё равно выходим.
    } finally {
      store.signOut();
    }
  }

  return { login, logout, refresh, restore };
}

export type Auth = ReturnType<typeof createAuth>;
