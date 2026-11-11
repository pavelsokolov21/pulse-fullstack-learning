import { describe, expect, it } from "vitest";

import {
  formatReportLatency,
  formatUptime,
  parseCacheStatus,
  parseMonitorId,
  pushLog,
  uptimeTone,
  type FetchLogEntry,
} from "@/lib/report";

describe("formatUptime", () => {
  it.each([
    [null, "—"],
    [100, "100 %"],
    [99.5, "99.5 %"],
    [66.667, "66.667 %"],
    [66.66666666, "66.667 %"],
    [0, "0 %"],
  ])("%s -> %s", (percent, expected) => {
    expect(formatUptime(percent)).toBe(expected);
  });
});

describe("formatReportLatency", () => {
  it.each([
    [null, "—"],
    [0, "0 мс"],
    [54.6, "55 мс"],
    [999, "999 мс"],
    [1500, "1.5 с"],
  ])("%s -> %s", (ms, expected) => {
    expect(formatReportLatency(ms)).toBe(expected);
  });
});

describe("uptimeTone", () => {
  it.each([
    [null, "none"],
    [100, "ok"],
    [99, "ok"],
    [98.999, "warn"],
    [95, "warn"],
    [94.999, "bad"],
    [0, "bad"],
  ] as const)("%s -> %s", (percent, expected) => {
    expect(uptimeTone(percent)).toBe(expected);
  });
});

describe("parseCacheStatus", () => {
  it.each([
    ["HIT", "HIT"],
    ["MISS", "MISS"],
    ["hit", null],
    ["", null],
    [null, null],
    ["BYPASS", null],
  ] as const)("%s -> %s", (header, expected) => {
    expect(parseCacheStatus(header)).toBe(expected);
  });
});

describe("parseMonitorId", () => {
  it.each([
    ["1", 1],
    ["35", 35],
    ["9007199254740991", 9007199254740991],
  ])("%s -> %i", (raw, expected) => {
    expect(parseMonitorId(raw)).toBe(expected);
  });

  it.each(["0", "-1", "01", "1.5", "abc", "", "1e3", " 1", "9007199254740992"])(
    "rejects %j",
    (raw) => {
      expect(parseMonitorId(raw)).toBeNull();
    },
  );

  it("rejects a missing or repeated parameter", () => {
    expect(parseMonitorId(undefined)).toBeNull();
    expect(parseMonitorId(["1", "2"])).toBeNull();
  });
});

describe("pushLog", () => {
  const entry = (id: number): FetchLogEntry => ({
    id,
    window: "7d",
    cache: "MISS",
    ms: 10,
  });

  it("puts the newest entry first", () => {
    expect(pushLog([entry(1)], entry(2)).map((e) => e.id)).toEqual([2, 1]);
  });

  it("keeps only the latest 8 entries", () => {
    let log: FetchLogEntry[] = [];
    for (let i = 1; i <= 12; i++) log = pushLog(log, entry(i));

    expect(log).toHaveLength(8);
    expect(log[0].id).toBe(12);
    expect(log[7].id).toBe(5);
  });

  it("does not mutate the previous log", () => {
    const before = [entry(1)];
    pushLog(before, entry(2));

    expect(before).toHaveLength(1);
  });
});
