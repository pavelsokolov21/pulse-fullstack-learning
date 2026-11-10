import { describe, expect, it } from "vitest";

import {
  buildLanes,
  describeEvent,
  percent,
  ticks,
  timelineEnd,
  verdict,
  type ConcurrencyRun,
  type TraceEvent,
} from "@/lib/concurrency";

function ev(
  call: number,
  at_ms: number,
  kind: TraceEvent["kind"],
  value: number | null = null,
  attempt = 1,
): TraceEvent {
  return { call, attempt, at_ms, kind, value };
}

describe("buildLanes", () => {
  it("builds hold and commit segments for an unprotected call", () => {
    const [lane] = buildLanes([
      ev(1, 0, "start"),
      ev(1, 5, "read", 3),
      ev(1, 405, "write", 4),
      ev(1, 408, "commit"),
    ]);

    expect(lane.call).toBe(1);
    expect(lane.segments.map((s) => [s.kind, s.fromMs, s.toMs])).toEqual([
      ["hold", 5, 405],
      ["commit", 405, 408],
    ]);
    expect(lane.segments[0].label).toBe("прочитал 3 → записал 4");
    expect(lane.conflicts).toEqual([]);
  });

  it("adds a waiting segment between the lock request and the read", () => {
    const [, second] = buildLanes([
      ev(1, 0, "start"),
      ev(1, 1, "lock_requested"),
      ev(1, 3, "read", 3),
      ev(1, 403, "write", 4),
      ev(1, 405, "commit"),
      ev(2, 0, "start"),
      ev(2, 1, "lock_requested"),
      ev(2, 410, "read", 4),
      ev(2, 810, "write", 5),
      ev(2, 812, "commit"),
    ]);

    expect(second.segments.map((s) => s.kind)).toEqual([
      "wait",
      "hold",
      "commit",
    ]);
    expect(second.segments[0]).toMatchObject({ fromMs: 1, toMs: 410 });
  });

  it("marks a conflict and keeps the retry attempt separate", () => {
    const [lane] = buildLanes([
      ev(1, 0, "start"),
      ev(1, 3, "read", 3),
      ev(1, 403, "conflict"),
      ev(1, 430, "start", null, 2),
      ev(1, 432, "read", 4, 2),
      ev(1, 832, "write", 5, 2),
      ev(1, 835, "commit", null, 2),
    ]);

    expect(lane.conflicts).toEqual([{ attempt: 1, atMs: 403 }]);
    expect(lane.segments.map((s) => [s.kind, s.attempt])).toEqual([
      ["hold", 1],
      ["hold", 2],
      ["commit", 2],
    ]);
    expect(lane.segments[0].label).toBe("прочитал 3 → отклонено");
  });

  it("returns one lane per call in call order, whatever the event order", () => {
    const lanes = buildLanes([ev(2, 1, "start"), ev(1, 0, "start")]);

    expect(lanes.map((l) => l.call)).toEqual([1, 2]);
  });

  it("returns nothing for no events", () => {
    expect(buildLanes([])).toEqual([]);
  });
});

describe("timeline scale", () => {
  it("ends at the later of the run time and the last event", () => {
    expect(timelineEnd([ev(1, 900, "commit")], 850)).toBe(900);
    expect(timelineEnd([ev(1, 10, "commit")], 850)).toBe(850);
    expect(timelineEnd([], 0)).toBe(1);
  });

  it.each([
    [400, 50, 9],
    [850, 200, 9],
    [2000, 250, 9],
  ])("ticks(%i) use step %i", (end, step, maxCount) => {
    const result = ticks(end);
    expect(result[1] - result[0]).toBe(step);
    expect(result.length).toBeLessThanOrEqual(maxCount);
  });

  it("starts at zero and never exceeds the end", () => {
    const result = ticks(850);
    expect(result[0]).toBe(0);
    expect(Math.max(...result)).toBeLessThanOrEqual(850);
  });

  it.each([
    [0, 1000, 0],
    [500, 1000, 50],
    [2000, 1000, 100],
    [-5, 1000, 0],
    [5, 0, 0],
  ])("percent(%i of %i) = %i", (ms, end, expected) => {
    expect(percent(ms, end)).toBe(expected);
  });
});

describe("describeEvent", () => {
  it("names the retry attempt", () => {
    expect(describeEvent(ev(1, 0, "start", null, 2))).toBe("попытка 2: начало");
    expect(describeEvent(ev(1, 0, "start"))).toBe("начало");
  });

  it("includes the value for reads and writes", () => {
    expect(describeEvent(ev(1, 0, "read", 3))).toBe("прочитал 3");
    expect(describeEvent(ev(1, 0, "write", 4))).toContain("записал 4");
  });
});

describe("verdict", () => {
  const base: ConcurrencyRun = {
    strategy: "unsafe",
    hold_ms: 400,
    initial: 3,
    expected: 5,
    final: 4,
    final_version: 1,
    elapsed_ms: 410,
    lost_update: true,
    events: [],
    calls: [],
  };

  it("reports a lost update with both numbers", () => {
    expect(verdict(base)).toEqual({
      tone: "bad",
      text: "Потеряно обновление: ожидали 5, в базе 4",
    });
  });

  it("reports a correct result", () => {
    expect(verdict({ ...base, final: 5, lost_update: false }).tone).toBe("ok");
  });
});
