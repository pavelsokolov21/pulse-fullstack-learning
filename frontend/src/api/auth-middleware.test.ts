import createClient from "openapi-fetch";
import { describe, expect, it, vi } from "vitest";

import { createAuthMiddleware } from "@/api/auth-middleware";
import type { paths } from "@/api/schema";

type Handler = (request: Request, call: number) => Response | Promise<Response>;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const EXPIRED = {
  code: "token_expired",
  message: "Срок действия токена истёк",
};
const MONITORS = [
  { id: 1, owner_id: 1, url: "https://a.test", interval: 60, timeout: 10 },
];

function setup(options: {
  handler: Handler;
  token?: string | null;
  refresh?: () => Promise<string | null>;
}) {
  let token = options.token === undefined ? "old" : options.token;
  const refresh = vi.fn(options.refresh ?? (async () => "new"));
  const seen: Request[] = [];
  const handler = vi.fn(options.handler);

  const client = createClient<paths>({
    baseUrl: "http://api.test",
    fetch: async (input) => {
      const request = input as Request;
      const call = seen.push(request.clone());
      // Как настоящий fetch: читает тело, второй раз его не прочитать.
      await request.arrayBuffer();
      return handler(request, call);
    },
  });
  client.use(
    createAuthMiddleware({
      getAccessToken: () => token,
      refresh: async () => {
        const next = await refresh();
        token = next;
        return next;
      },
    }),
  );
  return { client, refresh, seen };
}

describe("auth middleware", () => {
  it("sends the access token as a Bearer header", async () => {
    const { client, seen } = setup({ handler: () => json(200, MONITORS) });

    await client.GET("/monitors");

    expect(seen[0].headers.get("Authorization")).toBe("Bearer old");
  });

  it("sends no Authorization header without a token", async () => {
    const { client, seen } = setup({
      token: null,
      handler: () => json(200, []),
    });

    await client.GET("/monitors");

    expect(seen[0].headers.has("Authorization")).toBe(false);
  });

  it("refreshes once and repeats the request on an expired token", async () => {
    const { client, refresh, seen } = setup({
      handler: (request) =>
        request.headers.get("Authorization") === "Bearer new"
          ? json(200, MONITORS)
          : json(401, EXPIRED),
    });

    const { data, response } = await client.GET("/monitors");

    expect(response.status).toBe(200);
    expect(data).toEqual(MONITORS);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(seen.map((r) => r.headers.get("Authorization"))).toEqual([
      "Bearer old",
      "Bearer new",
    ]);
  });

  it("replays the request body when it repeats a POST", async () => {
    const { client, seen } = setup({
      handler: (request) =>
        request.headers.get("Authorization") === "Bearer new"
          ? json(201, MONITORS[0])
          : json(401, EXPIRED),
    });

    await client.POST("/monitors", {
      body: { url: "https://a.test", interval: "1m" },
    });

    expect(seen).toHaveLength(2);
    expect(seen[1].method).toBe("POST");
    expect(await seen[1].json()).toEqual({
      url: "https://a.test",
      interval: "1m",
    });
  });

  it("repeats at most once: a second 401 is returned as is", async () => {
    const { client, refresh, seen } = setup({
      handler: () => json(401, EXPIRED),
    });

    const { response, error } = await client.GET("/monitors");

    expect(response.status).toBe(401);
    expect(error).toEqual(EXPIRED);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(seen).toHaveLength(2);
  });

  it("returns the original 401 when refresh fails", async () => {
    const { client, seen } = setup({
      handler: () => json(401, EXPIRED),
      refresh: async () => null,
    });

    const { response, error } = await client.GET("/monitors");

    expect(response.status).toBe(401);
    expect(error).toEqual(EXPIRED);
    expect(seen).toHaveLength(1);
  });

  it.each(["invalid_token", "not_authenticated", "invalid_credentials"])(
    "does not refresh on a 401 with code %s",
    async (code) => {
      const { client, refresh, seen } = setup({
        handler: () => json(401, { code, message: "x" }),
      });

      const { response } = await client.GET("/monitors");

      expect(response.status).toBe(401);
      expect(refresh).not.toHaveBeenCalled();
      expect(seen).toHaveLength(1);
    },
  );

  it("does not refresh on a 401 with a body that is not JSON", async () => {
    const { client, refresh } = setup({
      handler: () => new Response("Unauthorized", { status: 401 }),
    });

    const { response } = await client.GET("/monitors");

    expect(response.status).toBe(401);
    expect(refresh).not.toHaveBeenCalled();
  });

  it.each([403, 404, 429, 500])("leaves a %i alone", async (status) => {
    const { client, refresh, seen } = setup({
      handler: () => json(status, { code: "token_expired", message: "x" }),
    });

    const { response } = await client.GET("/monitors");

    expect(response.status).toBe(status);
    expect(refresh).not.toHaveBeenCalled();
    expect(seen).toHaveLength(1);
  });

  it("lets the caller read the body of the response it gets back", async () => {
    // Тело 401 читалось в middleware; вызывающий код должен прочитать его снова.
    const { client } = setup({
      handler: () => json(401, { code: "invalid_token", message: "bad" }),
    });

    const { error } = await client.GET("/monitors");

    expect(error).toEqual({ code: "invalid_token", message: "bad" });
  });

  it("each concurrent request repeats with the fresh token", async () => {
    const { client, seen } = setup({
      handler: (request) =>
        request.headers.get("Authorization") === "Bearer new"
          ? json(200, MONITORS)
          : json(401, EXPIRED),
    });

    const results = await Promise.all([
      client.GET("/monitors"),
      client.GET("/monitors"),
      client.GET("/monitors"),
    ]);

    expect(results.map((r) => r.response.status)).toEqual([200, 200, 200]);
    expect(seen).toHaveLength(6);
  });

  it("does not refresh again when the token was renewed while the request was in flight", async () => {
    const sleep = (ms: number) =>
      new Promise((resolve) => setTimeout(resolve, ms));
    const { client, refresh, seen } = setup({
      refresh: async () => {
        await sleep(5);
        return "new";
      },
      handler: async (request, call) => {
        if (request.headers.get("Authorization") === "Bearer new") {
          return json(200, MONITORS);
        }
        if (call === 2) await sleep(40); // ответ приходит уже после обновления
        return json(401, EXPIRED);
      },
    });

    const results = await Promise.all([
      client.GET("/monitors"),
      client.GET("/monitors"),
    ]);

    expect(results.map((r) => r.response.status)).toEqual([200, 200]);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(seen).toHaveLength(4);
  });
});
