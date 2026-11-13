import { describe, expect, it, vi } from "vitest";

import {
  connectTaskStream,
  reconnectDelay,
  type StreamStatus,
} from "@/lib/task-stream";
import type { TaskEntry } from "@/lib/tasks";

const encoder = new TextEncoder();

function frame(id: string, checkId = 1, state = "queued"): string {
  const data = JSON.stringify({
    check_id: checkId,
    state,
    at: "2026-05-01T12:00:00Z",
    attempt: null,
    detail: null,
    retry_in: null,
  });
  return `event: task\nid: ${id}\ndata: ${data}\n\n`;
}

/** Ответ-поток: отдаёт куски и закрывается; `hang`: после кусков ждёт отмены. */
function streamResponse(
  chunks: (string | Uint8Array)[],
  signal: AbortSignal,
  options: { hang?: boolean; type?: string; status?: number } = {},
): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(
          typeof chunk === "string" ? encoder.encode(chunk) : chunk,
        );
      }
      if (options.hang) {
        signal.addEventListener("abort", () =>
          controller.error(new DOMException("aborted", "AbortError")),
        );
      } else {
        controller.close();
      }
    },
  });
  return new Response(body, {
    status: options.status ?? 200,
    headers: { "content-type": options.type ?? "text/event-stream" },
  });
}

type Call = { headers: Record<string, string>; signal: AbortSignal };
type Step = (call: Call) => Response | Promise<Response>;

function harness(
  steps: Step[],
  opts: { token?: string | null; blockSleep?: boolean } = {},
) {
  const calls: Call[] = [];
  const entries: TaskEntry[] = [];
  const statuses: StreamStatus[] = [];
  const delays: number[] = [];
  let token = opts.token === undefined ? "t1" : opts.token;
  const refresh = vi.fn<() => Promise<string | null>>();
  const closed = { done: false };

  const fetchImpl = ((_url: string, init: RequestInit) => {
    const call: Call = {
      headers: init.headers as Record<string, string>,
      signal: init.signal as AbortSignal,
    };
    calls.push(call);
    const step = steps[calls.length - 1];
    if (!step) {
      // Сценарий кончился: висим до отмены, как живое соединение.
      return new Promise<Response>((_, reject) =>
        call.signal.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        ),
      );
    }
    return Promise.resolve(step(call));
  }) as unknown as typeof fetch;

  const stop = connectTaskStream({
    url: "/api/tasks/stream",
    getToken: () => token,
    refresh: async () => {
      const fresh = await refresh();
      if (fresh !== null) token = fresh;
      return fresh;
    },
    onEntry: (e) => entries.push(e),
    onStatus: (s) => {
      if (statuses[statuses.length - 1] !== s) statuses.push(s);
    },
    fetchImpl,
    sleep: async (ms, signal) => {
      delays.push(ms);
      if (!opts.blockSleep) return;
      // Как настоящая пауза: длится, пока её не прервёт остановка.
      await new Promise<void>((resolve) =>
        signal.addEventListener("abort", () => resolve()),
      );
    },
    random: () => 0.5, // множитель разброса ровно 1
  });
  return {
    calls,
    entries,
    statuses,
    delays,
    refresh,
    setToken(value: string | null) {
      token = value;
    },
    stop() {
      closed.done = true;
      stop();
    },
  };
}

const settle = (ms = 20) => new Promise((r) => setTimeout(r, ms));

describe("connecting", () => {
  it("sends the token and asks for an event stream", async () => {
    const h = harness([(c) => streamResponse([], c.signal, { hang: true })]);
    await vi.waitFor(() => expect(h.calls).toHaveLength(1));

    expect(h.calls[0].headers.Authorization).toBe("Bearer t1");
    expect(h.calls[0].headers.Accept).toBe("text/event-stream");
    expect(h.calls[0].headers["Last-Event-ID"]).toBeUndefined();
    h.stop();
  });

  it("reports connecting, then open", async () => {
    const h = harness([(c) => streamResponse([], c.signal, { hang: true })]);
    await vi.waitFor(() => expect(h.statuses).toEqual(["connecting", "open"]));
    h.stop();
  });

  it("delivers events in order with their ids", async () => {
    const h = harness([
      (c) =>
        streamResponse([frame("1-0", 7), frame("2-0", 7, "sent")], c.signal, {
          hang: true,
        }),
    ]);

    await vi.waitFor(() => expect(h.entries).toHaveLength(2));

    expect(h.entries.map((e) => [e.id, e.event.state])).toEqual([
      ["1-0", "queued"],
      ["2-0", "sent"],
    ]);
    h.stop();
  });

  it("ignores comments, nameless messages and broken JSON", async () => {
    const h = harness([
      (c) =>
        streamResponse(
          [
            ": stream open\n\n",
            "data: no event name\n\n",
            "event: task\nid: 1-0\ndata: {not json\n\n",
            'event: task\nid: 2-0\ndata: {"check_id":"x"}\n\n',
            "event: task\ndata: no id\n\n",
            frame("3-0"),
            ": ping\n\n",
          ],
          c.signal,
          { hang: true },
        ),
    ]);

    await vi.waitFor(() => expect(h.entries).toHaveLength(1));

    expect(h.entries[0].id).toBe("3-0");
    h.stop();
  });

  it("decodes multi-byte characters split between chunks", async () => {
    const data = `event: task\nid: 1-0\ndata: {"check_id":1,"state":"retry","at":"2026-05-01T12:00:00Z","attempt":1,"detail":"привет","retry_in":1}\n\n`;
    const bytes = encoder.encode(data);
    const cut = bytes.indexOf(0xd0) + 1; // посреди буквы «п»
    const h = harness([
      (c) =>
        streamResponse([bytes.slice(0, cut), bytes.slice(cut)], c.signal, {
          hang: true,
        }),
    ]);

    await vi.waitFor(() => expect(h.entries).toHaveLength(1));

    expect(h.entries[0].event.detail).toBe("привет");
    h.stop();
  });
});

describe("reconnecting", () => {
  it("resumes from the last delivered id after the server closes the stream", async () => {
    const h = harness([
      (c) => streamResponse([frame("1-0"), frame("2-0")], c.signal),
      (c) => streamResponse([frame("3-0")], c.signal, { hang: true }),
    ]);

    await vi.waitFor(() => expect(h.entries).toHaveLength(3));

    expect(h.calls[1].headers["Last-Event-ID"]).toBe("2-0");
    expect(h.entries.map((e) => e.id)).toEqual(["1-0", "2-0", "3-0"]);
    expect(h.statuses).toEqual(["connecting", "open", "reconnecting", "open"]);
    h.stop();
  });

  it("reconnects quickly after a normal end, not with an error backoff", async () => {
    const h = harness([
      (c) => streamResponse([], c.signal),
      (c) => streamResponse([], c.signal, { hang: true }),
    ]);

    await vi.waitFor(() => expect(h.calls).toHaveLength(2));

    expect(h.delays).toEqual([500]);
    h.stop();
  });

  it("backs off after network errors and keeps the resume point", async () => {
    const fail: Step = () => Promise.reject(new TypeError("network"));
    const h = harness([
      (c) => streamResponse([frame("1-0")], c.signal),
      fail,
      fail,
      fail,
      (c) => streamResponse([], c.signal, { hang: true }),
    ]);

    await vi.waitFor(() => expect(h.calls).toHaveLength(5));

    // 500 мс после штатного конца, затем 1, 2, 4 с подряд.
    expect(h.delays).toEqual([500, 1000, 2000, 4000]);
    expect(h.calls[4].headers["Last-Event-ID"]).toBe("1-0");
    h.stop();
  });

  it("starts the backoff over after a successful connection", async () => {
    const fail: Step = () => Promise.reject(new TypeError("network"));
    const h = harness([
      fail,
      fail,
      (c) => streamResponse([], c.signal),
      fail,
      (c) => streamResponse([], c.signal, { hang: true }),
    ]);

    await vi.waitFor(() => expect(h.calls).toHaveLength(5));

    expect(h.delays).toEqual([1000, 2000, 500, 1000]);
    h.stop();
  });

  it("treats an error status as a failure and retries", async () => {
    const h = harness([
      () => new Response("boom", { status: 502 }),
      (c) => streamResponse([], c.signal, { hang: true }),
    ]);

    await vi.waitFor(() => expect(h.calls).toHaveLength(2));

    expect(h.statuses).toEqual(["connecting", "reconnecting", "open"]);
    h.stop();
  });

  it("treats an HTML answer with status 200 as a failure", async () => {
    // Прокси или страница ошибки вместо потока: не «открыто».
    const h = harness([
      (c) => streamResponse(["<html>"], c.signal, { type: "text/html" }),
      (c) => streamResponse([], c.signal, { hang: true }),
    ]);

    await vi.waitFor(() => expect(h.calls).toHaveLength(2));

    expect(h.entries).toEqual([]);
    expect(h.statuses.slice(0, 2)).toEqual(["connecting", "reconnecting"]);
    h.stop();
  });
});

describe("authorization", () => {
  it("stops on 403 without retrying", async () => {
    const h = harness([() => new Response("{}", { status: 403 })]);

    await vi.waitFor(() => expect(h.statuses).toContain("forbidden"));
    await settle();

    expect(h.calls).toHaveLength(1);
    expect(h.delays).toEqual([]);
  });

  it("refreshes the token on 401 and retries immediately with the new one", async () => {
    const h = harness([
      () => new Response("{}", { status: 401 }),
      (c) => streamResponse([], c.signal, { hang: true }),
    ]);
    h.refresh.mockResolvedValue("t2");

    await vi.waitFor(() => expect(h.calls).toHaveLength(2));

    expect(h.refresh).toHaveBeenCalledTimes(1);
    expect(h.calls[0].headers.Authorization).toBe("Bearer t1");
    expect(h.calls[1].headers.Authorization).toBe("Bearer t2");
    expect(h.delays).toEqual([]); // без паузы
    h.stop();
  });

  it("reports signed-out when the session cannot be refreshed", async () => {
    const h = harness([() => new Response("{}", { status: 401 })]);
    // refresh-токен отозван: клиент auth сам выходит из сессии, токена больше нет.
    h.refresh.mockImplementation(async () => {
      h.setToken(null);
      return null;
    });

    await vi.waitFor(() => expect(h.statuses).toContain("signed-out"));
    await settle();

    expect(h.calls).toHaveLength(1);
  });

  it("waits and retries when refresh failed only because of the network", async () => {
    const h = harness([
      () => new Response("{}", { status: 401 }),
      (c) => streamResponse([], c.signal, { hang: true }),
    ]);
    // Сеть моргнула при refresh, токен в памяти остался: сессия не потеряна.
    h.refresh.mockResolvedValue(null);

    await vi.waitFor(() => expect(h.calls).toHaveLength(2));

    expect(h.statuses).not.toContain("signed-out");
    expect(h.delays).toEqual([1000]);
    h.stop();
  });

  it("gives up refreshing after repeated 401 and does not loop forever", async () => {
    const unauthorized: Step = () => new Response("{}", { status: 401 });
    const h = harness([unauthorized, unauthorized, unauthorized, unauthorized]);
    h.refresh.mockResolvedValue("t-next");

    await settle(100);

    expect(h.refresh.mock.calls.length).toBeLessThanOrEqual(2);
    h.stop();
  });
});

describe("closing", () => {
  it("aborts the request and makes no further attempts", async () => {
    const h = harness([(c) => streamResponse([], c.signal, { hang: true })]);
    await vi.waitFor(() => expect(h.statuses).toContain("open"));

    h.stop();
    await settle();

    expect(h.calls[0].signal.aborted).toBe(true);
    expect(h.calls).toHaveLength(1);
    expect(h.statuses[h.statuses.length - 1]).toBe("open"); // «reconnecting» не мигает
  });

  it("does not connect again if closed during the backoff", async () => {
    const h = harness([() => Promise.reject(new TypeError("network"))], {
      blockSleep: true,
    });
    await vi.waitFor(() => expect(h.delays).toHaveLength(1));

    h.stop();
    await settle();

    expect(h.calls).toHaveLength(1);
  });
});

describe("reconnectDelay", () => {
  it.each([
    [1, 1000],
    [2, 2000],
    [3, 4000],
    [4, 8000],
    [5, 15000],
    [20, 15000],
  ])("after %i failures waits %i ms (without jitter)", (failures, ms) => {
    expect(reconnectDelay(failures, () => 0.5)).toBe(ms);
  });

  it("spreads the delay by ±25%", () => {
    expect(reconnectDelay(3, () => 0)).toBe(3000);
    expect(reconnectDelay(3, () => 0.999999)).toBe(5000);
  });

  it("never waits less than the base even for zero failures", () => {
    expect(reconnectDelay(0, () => 0.5)).toBe(1000);
  });
});
