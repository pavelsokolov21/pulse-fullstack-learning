import createClient from "openapi-fetch";
import { describe, expect, it } from "vitest";

import type { paths } from "@/api/schema";
import { createAuth } from "@/lib/auth";
import { createSessionStore } from "@/lib/session-store";

function json(
  status: number,
  body: unknown,
  headers: HeadersInit = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const TOKENS = { access_token: "fresh", token_type: "bearer", expires_in: 900 };

function setup(handler: (request: Request) => Response | Promise<Response>) {
  const seen: Request[] = [];
  const client = createClient<paths>({
    baseUrl: "http://api.test",
    fetch: async (input) => {
      const request = input as Request;
      seen.push(request.clone());
      // Как настоящий fetch: читает тело, второй раз его не прочитать.
      await request.arrayBuffer();
      return handler(request);
    },
  });
  const store = createSessionStore();
  return { auth: createAuth(client, store), store, seen };
}

describe("login", () => {
  it("sends a urlencoded form with username, password and scope", async () => {
    const { auth, seen } = setup(() => json(200, TOKENS));

    await auth.login("a@b.test", "p&ss word");

    const request = seen[0];
    expect(request.url).toBe("http://api.test/auth/login");
    expect(request.headers.get("Content-Type")).toContain(
      "application/x-www-form-urlencoded",
    );
    const form = new URLSearchParams(await request.text());
    expect(Object.fromEntries(form)).toEqual({
      username: "a@b.test",
      password: "p&ss word",
      scope: "",
    });
  });

  it("stores the access token on success", async () => {
    const { auth, store } = setup(() => json(200, TOKENS));

    const result = await auth.login("a@b.test", "pw");

    expect(result).toEqual({ ok: true });
    expect(store.getAccessToken()).toBe("fresh");
  });

  it("reports wrong credentials without touching the session", async () => {
    const { auth, store } = setup(() =>
      json(401, { code: "invalid_credentials", message: "x" }),
    );

    const result = await auth.login("a@b.test", "bad");

    expect(result).toEqual({ ok: false, reason: "invalid_credentials" });
    expect(store.getAccessToken()).toBeNull();
  });

  it("reports rate limiting with the Retry-After value", async () => {
    const { auth } = setup(() =>
      json(
        429,
        { code: "rate_limited", message: "x" },
        { "Retry-After": "42" },
      ),
    );

    expect(await auth.login("a@b.test", "pw")).toEqual({
      ok: false,
      reason: "rate_limited",
      retryAfter: 42,
    });
  });

  it("copes with a 429 without Retry-After", async () => {
    const { auth } = setup(() =>
      json(429, { code: "rate_limited", message: "x" }),
    );

    expect(await auth.login("a@b.test", "pw")).toEqual({
      ok: false,
      reason: "rate_limited",
      retryAfter: null,
    });
  });

  it("reports an unreachable server", async () => {
    const { auth } = setup(() => {
      throw new TypeError("fetch failed");
    });

    expect(await auth.login("a@b.test", "pw")).toEqual({
      ok: false,
      reason: "unavailable",
    });
  });
});

describe("refresh", () => {
  it("calls /auth/refresh and stores the new token", async () => {
    const { auth, store, seen } = setup(() => json(200, TOKENS));

    const token = await auth.refresh();

    expect(token).toBe("fresh");
    expect(store.getAccessToken()).toBe("fresh");
    expect(seen[0].method).toBe("POST");
    expect(seen[0].url).toBe("http://api.test/auth/refresh");
  });

  it("shares one request between concurrent callers", async () => {
    // Refresh-токен одноразовый: второй вызов сервер счёл бы кражей.
    const { auth, seen } = setup(() => json(200, TOKENS));

    const tokens = await Promise.all([
      auth.refresh(),
      auth.refresh(),
      auth.refresh(),
    ]);

    expect(tokens).toEqual(["fresh", "fresh", "fresh"]);
    expect(seen).toHaveLength(1);
  });

  it("makes a new request once the previous one has finished", async () => {
    const { auth, seen } = setup(() => json(200, TOKENS));

    await auth.refresh();
    await auth.refresh();

    expect(seen).toHaveLength(2);
  });

  it("ends the session on 401", async () => {
    const { auth, store } = setup(() =>
      json(401, { code: "invalid_refresh_token", message: "x" }),
    );
    store.signIn("stale");

    expect(await auth.refresh()).toBeNull();
    expect(store.getState()).toEqual({ status: "anonymous" });
  });

  it("keeps the session when the server is unreachable", async () => {
    const { auth, store } = setup(() => {
      throw new TypeError("fetch failed");
    });
    store.signIn("still-valid");

    expect(await auth.refresh()).toBeNull();
    expect(store.getAccessToken()).toBe("still-valid");
  });

  it("keeps the session on a 5xx", async () => {
    const { auth, store } = setup(() =>
      json(500, { code: "internal_error", message: "x" }),
    );
    store.signIn("still-valid");

    expect(await auth.refresh()).toBeNull();
    expect(store.getAccessToken()).toBe("still-valid");
  });
});

describe("restore", () => {
  it("signs in from the refresh cookie", async () => {
    const { auth, store } = setup(() => json(200, TOKENS));

    await auth.restore();

    expect(store.getState().status).toBe("authenticated");
  });

  it("becomes anonymous when there is no session", async () => {
    const { auth, store } = setup(() =>
      json(401, { code: "not_authenticated", message: "x" }),
    );

    await auth.restore();

    expect(store.getState()).toEqual({ status: "anonymous" });
  });

  it("becomes anonymous when the server is unreachable", async () => {
    const { auth, store } = setup(() => {
      throw new TypeError("fetch failed");
    });

    await auth.restore();

    expect(store.getState()).toEqual({ status: "anonymous" });
  });

  it("runs once even when called twice (React strict mode)", async () => {
    const { auth, seen } = setup(() => json(200, TOKENS));

    await Promise.all([auth.restore(), auth.restore()]);
    await auth.restore();

    expect(seen).toHaveLength(1);
  });
});

describe("logout", () => {
  it("calls /auth/logout and clears the session", async () => {
    const { auth, store, seen } = setup(
      () => new Response(null, { status: 204 }),
    );
    store.signIn("abc");

    await auth.logout();

    expect(seen[0].url).toBe("http://api.test/auth/logout");
    expect(seen[0].method).toBe("POST");
    expect(store.getState()).toEqual({ status: "anonymous" });
  });

  it("clears the session even if the server is unreachable", async () => {
    const { auth, store } = setup(() => {
      throw new TypeError("fetch failed");
    });
    store.signIn("abc");

    await auth.logout();

    expect(store.getState()).toEqual({ status: "anonymous" });
  });
});
