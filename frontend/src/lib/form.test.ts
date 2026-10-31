import { describe, expect, it } from "vitest";

import { toFormBody } from "@/lib/form";

describe("toFormBody", () => {
  it("joins fields with & and =", () => {
    expect(toFormBody({ username: "a", password: "b" })).toBe(
      "username=a&password=b",
    );
  });

  it("escapes characters that would break the body", () => {
    const body = toFormBody({ password: "p&ss=w rd+%é" });

    expect(body).toBe("password=p%26ss%3Dw+rd%2B%25%C3%A9");
    expect(new URLSearchParams(body).get("password")).toBe("p&ss=w rd+%é");
  });

  it("skips null and undefined but keeps empty strings", () => {
    expect(toFormBody({ a: null, b: undefined, scope: "" })).toBe("scope=");
  });

  it("is not JSON", () => {
    expect(toFormBody({ a: 1 })).not.toContain("{");
  });
});
