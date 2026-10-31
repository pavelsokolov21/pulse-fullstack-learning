import { describe, expect, it, vi } from "vitest";

import { createSessionStore } from "@/lib/session-store";

describe("session store", () => {
  it("starts unknown without a token", () => {
    const store = createSessionStore();

    expect(store.getState()).toEqual({ status: "unknown" });
    expect(store.getAccessToken()).toBeNull();
  });

  it("holds the token after sign in and drops it after sign out", () => {
    const store = createSessionStore();

    store.signIn("abc");
    expect(store.getState()).toEqual({
      status: "authenticated",
      accessToken: "abc",
    });
    expect(store.getAccessToken()).toBe("abc");

    store.signOut();
    expect(store.getState()).toEqual({ status: "anonymous" });
    expect(store.getAccessToken()).toBeNull();
  });

  it("notifies subscribers with a new state object each time", () => {
    const store = createSessionStore();
    const listener = vi.fn();
    store.subscribe(listener);
    const before = store.getState();

    store.signIn("abc");

    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getState()).not.toBe(before);
  });

  it("stops notifying after unsubscribe", () => {
    const store = createSessionStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);

    unsubscribe();
    store.signIn("abc");

    expect(listener).not.toHaveBeenCalled();
  });

  it("does not keep the token in web storage", () => {
    // Токен в localStorage читается любым XSS-скриптом.
    const store = createSessionStore();
    const setItem = vi.fn();
    vi.stubGlobal("localStorage", { setItem });
    vi.stubGlobal("sessionStorage", { setItem });

    store.signIn("abc");

    expect(setItem).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
