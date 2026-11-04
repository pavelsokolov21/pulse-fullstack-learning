import { describe, expect, it } from "vitest";

import {
  clampPage,
  offsetFor,
  pageCount,
  visibleRange,
} from "@/lib/pagination";

describe("pageCount", () => {
  it.each([
    [0, 10, 1],
    [1, 10, 1],
    [10, 10, 1],
    [11, 10, 2],
    [47, 10, 5],
    [50, 10, 5],
  ])("%i items by %i -> %i pages", (total, size, expected) => {
    expect(pageCount(total, size)).toBe(expected);
  });
});

describe("offsetFor", () => {
  it.each([
    [1, 10, 0],
    [2, 10, 10],
    [5, 20, 80],
  ])("page %i of size %i starts at %i", (page, size, expected) => {
    expect(offsetFor(page, size)).toBe(expected);
  });
});

describe("clampPage", () => {
  it("keeps a valid page", () => {
    expect(clampPage(3, 47, 10)).toBe(3);
  });

  it("moves back when the page no longer exists", () => {
    expect(clampPage(6, 47, 10)).toBe(5);
    expect(clampPage(2, 10, 10)).toBe(1);
  });

  it("never goes below the first page", () => {
    expect(clampPage(0, 47, 10)).toBe(1);
    expect(clampPage(-3, 0, 10)).toBe(1);
  });

  it("stays on page 1 for an empty list", () => {
    expect(clampPage(4, 0, 10)).toBe(1);
  });
});

describe("visibleRange", () => {
  it("describes a full page", () => {
    expect(visibleRange(2, 10, 10)).toEqual({ from: 11, to: 20 });
  });

  it("describes a partial last page", () => {
    expect(visibleRange(5, 7, 10)).toEqual({ from: 41, to: 47 });
  });

  it("is null when nothing is shown", () => {
    expect(visibleRange(1, 0, 10)).toBeNull();
  });
});
