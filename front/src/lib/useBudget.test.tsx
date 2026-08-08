import { act, renderHook } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BUDGET_STORAGE_KEY, resetBudgetMemoryForTests, useBudget } from "./budget";

/**
 * This repo's jsdom exposes a localStorage getter whose object has NO methods, so calling
 * getItem/setItem throws — which is exactly the hook's "storage unavailable" path. To test the
 * happy path we install a functional mock; to test failures we spy on that mock.
 */
function installMockStorage(): Storage {
  const store = new Map<string, string>();
  const mock = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => {
      store.set(k, String(v));
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
  Object.defineProperty(window, "localStorage", { value: mock, configurable: true });
  return mock;
}

describe("useBudget (single-owner budget hook — codex D6/H3)", () => {
  let storage: Storage;

  beforeEach(() => {
    storage = installMockStorage();
    resetBudgetMemoryForTests();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts unset, persists a value, and restores it after unmount → remount", () => {
    const first = renderHook(() => useBudget());
    expect(first.result.current.budget).toBeNull();
    act(() => first.result.current.setBudget(550));
    expect(first.result.current.budget).toBe(550);
    expect(storage.getItem(BUDGET_STORAGE_KEY)).toBe("550");
    first.unmount();

    const second = renderHook(() => useBudget());
    expect(second.result.current.budget).toBe(550);
  });

  it("ignores corrupted stored values (validation on restore)", () => {
    for (const bad of ["abc", "-100", "99", "100.5", "1e4", ""]) {
      resetBudgetMemoryForTests();
      storage.setItem(BUDGET_STORAGE_KEY, bad);
      const { result, unmount } = renderHook(() => useBudget());
      expect(result.current.budget, `stored "${bad}" must be rejected`).toBeNull();
      unmount();
    }
  });

  it("keeps the value for the session when localStorage WRITES throw (module mirror)", () => {
    vi.spyOn(storage, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    const first = renderHook(() => useBudget());
    act(() => first.result.current.setBudget(10000));
    expect(first.result.current.budget).toBe(10000);
    first.unmount();

    // remount within the same session — the module-level mirror must survive
    const second = renderHook(() => useBudget());
    expect(second.result.current.budget).toBe(10000);
  });

  it("keeps working when localStorage READS throw", () => {
    vi.spyOn(storage, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    const { result } = renderHook(() => useBudget());
    expect(result.current.budget).toBeNull();
    act(() => result.current.setBudget(300));
    expect(result.current.budget).toBe(300);
  });

  it("keeps working when removeItem throws while clearing", () => {
    const { result } = renderHook(() => useBudget());
    act(() => result.current.setBudget(500));
    vi.spyOn(storage, "removeItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    act(() => result.current.setBudget(null));
    expect(result.current.budget).toBeNull();
  });

  it("survives an entirely method-less localStorage (this jsdom's default shape)", () => {
    Object.defineProperty(window, "localStorage", { value: {}, configurable: true });
    const { result, unmount } = renderHook(() => useBudget());
    expect(result.current.budget).toBeNull();
    act(() => result.current.setBudget(800));
    expect(result.current.budget).toBe(800);
    unmount();
    const second = renderHook(() => useBudget());
    expect(second.result.current.budget).toBe(800); // session mirror
  });

  it("behaves identically under <StrictMode> (double evaluation is safe)", () => {
    const { result, unmount } = renderHook(() => useBudget(), { wrapper: StrictMode });
    expect(result.current.budget).toBeNull();
    act(() => result.current.setBudget(700));
    expect(result.current.budget).toBe(700);
    expect(storage.getItem(BUDGET_STORAGE_KEY)).toBe("700");
    unmount();

    const second = renderHook(() => useBudget(), { wrapper: StrictMode });
    expect(second.result.current.budget).toBe(700);
  });
});
