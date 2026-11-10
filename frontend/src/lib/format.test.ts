import { describe, expect, it } from "vitest";

import {
  formatAgo,
  formatInterval,
  formatLatency,
  formatRole,
  parseApiTime,
} from "@/lib/format";

describe("formatInterval", () => {
  it.each([
    [30, "30 с"],
    [90, "90 с"],
    [60, "1 мин"],
    [300, "5 мин"],
    [3600, "1 ч"],
    [7200, "2 ч"],
    [5400, "90 мин"],
  ])("%i seconds -> %s", (seconds, expected) => {
    expect(formatInterval(seconds)).toBe(expected);
  });
});

describe("formatRole", () => {
  it.each([
    ["admin", "администратор"],
    ["manager", "менеджер"],
    ["viewer", "наблюдатель"],
  ])("%s -> %s", (role, expected) => {
    expect(formatRole(role)).toBe(expected);
  });

  it("shows an unknown role as is", () => {
    expect(formatRole("auditor")).toBe("auditor");
  });
});

describe("formatLatency", () => {
  it.each([
    [0, "0 мс"],
    [54.6, "55 мс"],
    [999, "999 мс"],
    [1000, "1.0 с"],
    [1234, "1.2 с"],
    [10000, "10.0 с"],
  ])("%d ms -> %s", (ms, expected) => {
    expect(formatLatency(ms)).toBe(expected);
  });
});

describe("formatAgo", () => {
  const now = new Date("2026-01-10T12:00:00Z");

  it.each([
    ["2026-01-10T11:59:30Z", "только что"],
    ["2026-01-10T11:59:00Z", "1 мин назад"],
    ["2026-01-10T11:15:00Z", "45 мин назад"],
    ["2026-01-10T11:00:00Z", "1 ч назад"],
    ["2026-01-10T00:30:00Z", "11 ч назад"],
    ["2026-01-09T12:00:00Z", "1 дн. назад"],
    ["2025-12-11T12:00:00Z", "30 дн. назад"],
  ])("%s -> %s", (iso, expected) => {
    expect(formatAgo(iso, now)).toBe(expected);
  });

  it("shows a timestamp from the future as just now", () => {
    expect(formatAgo("2026-01-10T12:05:00Z", now)).toBe("только что");
  });
});

describe("parseApiTime", () => {
  it("accepts microseconds as PostgreSQL returns them", () => {
    expect(parseApiTime("2026-11-05T13:28:30.084049Z").toISOString()).toBe(
      "2026-11-05T13:28:30.084Z",
    );
  });

  it.each([
    ["2026-11-05T13:28:30Z", "2026-11-05T13:28:30.000Z"],
    ["2026-11-05T13:28:30.5Z", "2026-11-05T13:28:30.500Z"],
    ["2026-11-05T13:28:30.084Z", "2026-11-05T13:28:30.084Z"],
    ["2026-11-05T13:28:30.084049+03:00", "2026-11-05T10:28:30.084Z"],
  ])("%s", (iso, expected) => {
    expect(parseApiTime(iso).toISOString()).toBe(expected);
  });
});

describe("formatAgo with unusable input", () => {
  const now = new Date("2026-01-10T12:00:00Z");

  it("handles microseconds in the timestamp", () => {
    expect(formatAgo("2026-01-10T10:59:59.123456Z", now)).toBe("1 ч назад");
  });

  it("shows a dash instead of NaN for an unparsable time", () => {
    expect(formatAgo("не дата", now)).toBe("—");
  });

  it("shows a dash when the reference time is invalid", () => {
    expect(formatAgo("2026-01-10T11:00:00Z", new Date(Number.NaN))).toBe("—");
  });
});
