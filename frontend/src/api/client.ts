import createClient from "openapi-fetch";

import { createAuthMiddleware } from "@/api/auth-middleware";
import type { paths } from "@/api/schema";
import { createAuth } from "@/lib/auth";
import { API_BASE_URL } from "@/lib/config";
import { createSessionStore } from "@/lib/session-store";

// Запросы на тот же origin: cookie с refresh-токеном браузер шлёт сам, без
// `credentials: "include"`.
const clientOptions = { baseUrl: API_BASE_URL };

export const sessionStore = createSessionStore();

// Без middleware: для входа, выхода и обновления (см. createAuth).
const bareClient = createClient<paths>(clientOptions);
export const auth = createAuth(bareClient, sessionStore);

// Для остальных запросов: сам подставляет токен и лечит истёкший.
export const api = createClient<paths>(clientOptions);
api.use(
  createAuthMiddleware({
    getAccessToken: sessionStore.getAccessToken,
    refresh: auth.refresh,
  }),
);
