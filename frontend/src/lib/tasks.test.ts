import { describe, expect, it } from "vitest";

import {
  buildLanes,
  describeEvent,
  offsetSeconds,
  retryProgress,
  retryRemaining,
  type TaskEvent,
} from "@/lib/tasks";

const T0 = Date.parse("2026-05-01T12:00:00Z");

function ev(
  checkId: number,
  state: TaskEvent["state"],
  seconds: number,
  extra: Partial<TaskEvent> = {},
): TaskEvent {
  return {
    check_id: checkId,
    state,
    at: new Date(T0 + seconds * 1000).toISOString(),
    attempt: null,
    detail: null,
    retry_in: null,
    ...extra,
  };
}

describe("buildLanes", () => {
  it("returns nothing for an empty journal", () => {
    expect(buildLanes([])).toEqual([]);
  });

  it("groups events by check and reads them oldest first", () => {
    // Журнал отдаёт новые первыми.
    const lanes = buildLanes([
      ev(1, "sent", 3, { attempt: 2 }),
      ev(1, "started", 2, { attempt: 2 }),
      ev(1, "queued", 0),
    ]);

    expect(lanes).toHaveLength(1);
    expect(lanes[0].events.map((e) => e.state)).toEqual([
      "queued",
      "started",
      "sent",
    ]);
  });

  it("keeps recording order for events with the same time", () => {
    const lanes = buildLanes([
      ev(1, "sent", 1),
      ev(1, "started", 1),
      ev(1, "queued", 1),
    ]);

    expect(lanes[0].events.map((e) => e.state)).toEqual([
      "queued",
      "started",
      "sent",
    ]);
  });

  it("puts the lane that changed last on top", () => {
    const lanes = buildLanes([
      ev(2, "queued", 10),
      ev(1, "sent", 5),
      ev(1, "queued", 0),
    ]);

    expect(lanes.map((l) => l.checkId)).toEqual([2, 1]);
  });

  it("does not mutate its input", () => {
    const input = [ev(1, "started", 1), ev(1, "queued", 0)];
    const copy = structuredClone(input);

    buildLanes(input);

    expect(input).toEqual(copy);
  });

  it.each([
    ["queued", "queued"],
    ["started", "running"],
    ["retry", "waiting"],
    ["sent", "done"],
    ["duplicate", "done"],
    ["skipped", "done"],
    ["failed", "failed"],
  ] as const)("last event %s means status %s", (state, status) => {
    const [lane] = buildLanes([ev(1, state, 1), ev(1, "queued", 0)]);

    expect(lane.status).toBe(status);
  });

  it("takes the status from the latest event, not the first", () => {
    const [lane] = buildLanes([
      ev(1, "sent", 9),
      ev(1, "retry", 5, { retry_in: 2 }),
      ev(1, "started", 1),
    ]);

    expect(lane.status).toBe("done");
  });
});

describe("retryRemaining", () => {
  const waiting = () => {
    const [lane] = buildLanes([
      ev(1, "retry", 10, { attempt: 1, retry_in: 4 }),
      ev(1, "queued", 0),
    ]);
    return lane;
  };

  it("counts down to the next attempt", () => {
    expect(retryRemaining(waiting(), new Date(T0 + 11_000))).toBeCloseTo(3);
  });

  it("never goes below zero", () => {
    expect(retryRemaining(waiting(), new Date(T0 + 60_000))).toBe(0);
  });

  it("is null when no retry is awaited", () => {
    const [lane] = buildLanes([ev(1, "started", 1), ev(1, "queued", 0)]);

    expect(retryRemaining(lane, new Date(T0 + 2000))).toBeNull();
  });

  it("is null once the retry has started", () => {
    const [lane] = buildLanes([
      ev(1, "started", 15, { attempt: 2 }),
      ev(1, "retry", 10, { retry_in: 4 }),
    ]);

    expect(retryRemaining(lane, new Date(T0 + 16_000))).toBeNull();
  });
});

describe("retryProgress", () => {
  const lane = () =>
    buildLanes([ev(1, "retry", 10, { attempt: 1, retry_in: 4 })])[0];

  it.each([
    [10, 0],
    [12, 0.5],
    [14, 1],
    [99, 1],
    [5, 0],
  ])("at +%is progress is %d", (seconds, expected) => {
    expect(retryProgress(lane(), new Date(T0 + seconds * 1000))).toBeCloseTo(
      expected,
    );
  });

  it("is null when there is nothing to wait for", () => {
    const [done] = buildLanes([ev(1, "sent", 1)]);

    expect(retryProgress(done, new Date(T0))).toBeNull();
  });

  it("treats a zero pause as already elapsed", () => {
    const [zero] = buildLanes([ev(1, "retry", 1, { retry_in: 0 })]);

    expect(retryProgress(zero, new Date(T0 + 1000))).toBe(1);
  });
});

describe("offsetSeconds", () => {
  it("is measured from the first event of the lane", () => {
    const [lane] = buildLanes([
      ev(1, "started", 1.9),
      ev(1, "queued", 0),
    ]);

    expect(offsetSeconds(lane, lane.events[0])).toBe(0);
    expect(offsetSeconds(lane, lane.events[1])).toBeCloseTo(1.9);
  });
});

describe("describeEvent", () => {
  it.each([
    [ev(1, "queued", 0), "в очереди", "neutral"],
    [ev(1, "started", 0, { attempt: 3 }), "попытка 3", "active"],
    [ev(1, "retry", 0, { retry_in: 3.44 }), "повтор через 3.4 с", "warn"],
    [ev(1, "sent", 0), "отправлено", "ok"],
    [ev(1, "duplicate", 0), "уже отправляли", "ok"],
    [ev(1, "skipped", 0), "сообщать не о чем", "neutral"],
    [ev(1, "failed", 0), "провал", "bad"],
  ] as const)("%j", (event, label, tone) => {
    expect(describeEvent(event)).toEqual({ label, tone });
  });

  it("survives a retry without a delay", () => {
    expect(describeEvent(ev(1, "retry", 0)).label).toBe("повтор");
  });
});
