import { describe, expect, it } from "vitest";

import { formatInterval, formatRole } from "@/lib/format";

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
