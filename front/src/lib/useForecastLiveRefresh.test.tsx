import { act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RaceDetail } from "../api/types";
import { renderWithProviders } from "../tests/utils";
import { isUpcomingForecast, useForecastLiveRefresh } from "./useForecastLiveRefresh";

const NOW = Date.parse("2026-11-07T04:00:00Z");
const upcoming = { race_date: "2026-11-07", post_time: "2026-11-07T05:00:00Z", has_results: false };

function Watch({ raceId = "race-a", race = upcoming }: { raceId?: string; race?: Pick<RaceDetail, "race_date" | "post_time" | "has_results"> }) {
  useForecastLiveRefresh(raceId, race, 1000);
  return null;
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("forecast live updates", () => {
  it("uses known future post times and permits only future dates when the time is missing", () => {
    expect(isUpcomingForecast(upcoming, NOW)).toBe(true);
    expect(isUpcomingForecast({ ...upcoming, post_time: "2026-11-07T04:00:00Z" }, NOW)).toBe(false);
    expect(isUpcomingForecast({ ...upcoming, has_results: true }, NOW)).toBe(false);
    expect(isUpcomingForecast({ ...upcoming, post_time: "invalid" }, NOW)).toBe(false);
    expect(isUpcomingForecast({ ...upcoming, post_time: null }, NOW)).toBe(false);
    expect(isUpcomingForecast({ ...upcoming, race_date: "2026-11-08", post_time: null }, NOW)).toBe(true);
    // 16:00 UTC is already the following calendar day in Japan.
    expect(isUpcomingForecast({ ...upcoming, race_date: "2026-11-08", post_time: null }, Date.parse("2026-11-07T16:00:00Z"))).toBe(false);
  });

  it("observes both models, entries and odds without enqueuing any work", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const fetch = vi.spyOn(globalThis, "fetch");
    const { queryClient } = renderWithProviders(<Watch />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await act(async () => { vi.advanceTimersByTime(1000); });
    for (const key of ["race", "predictions", "market-ev", "attention", "race-chaos", "odds"]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: [key, "race-a"], refetchType: "active" });
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("pauses periodic updates while hidden and refreshes on return", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const { queryClient } = renderWithProviders(<Watch />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await act(async () => { vi.advanceTimersByTime(2000); });
    expect(invalidate).not.toHaveBeenCalled();
    visibility.mockReturnValue("visible");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["predictions", "race-a"], refetchType: "active" });
  });

  it("stops reading when post time passes even if no result has arrived", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const { queryClient } = renderWithProviders(<Watch race={{ ...upcoming, post_time: "2026-11-07T04:00:01Z" }} />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("moves observation to the new race and cleans up timers and visibility events", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const view = renderWithProviders(<Watch />);
    const invalidate = vi.spyOn(view.queryClient, "invalidateQueries");
    view.rerender(<Watch raceId="race-b" />);
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(invalidate.mock.calls.every(([filter]) => filter?.queryKey?.[1] === "race-b")).toBe(true);
    invalidate.mockClear();
    view.unmount();
    await act(async () => {
      vi.advanceTimersByTime(2000);
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(invalidate).not.toHaveBeenCalled();
  });
});
