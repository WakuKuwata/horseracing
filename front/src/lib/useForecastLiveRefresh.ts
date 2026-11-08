import { useEffect } from "react";

import { useQueryClient } from "@tanstack/react-query";

import type { RaceDetail } from "../api/types";

type RaceTiming = Pick<RaceDetail, "race_date" | "post_time" | "has_results">;

export function isUpcomingForecast(race: RaceTiming | undefined, now = Date.now()): boolean {
  if (!race || race.has_results) return false;
  if (race.post_time) {
    const post = Date.parse(race.post_time);
    return Number.isFinite(post) && post > now;
  }
  const todayJst = new Date(now + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
  return !!race.race_date && race.race_date > todayJst;
}

/** Observe saved results of background jobs. Opening a page never POSTs work or starts scraping. */
export function useForecastLiveRefresh(raceId: string, race: RaceTiming | undefined, pollMs = 30_000) {
  const client = useQueryClient();
  const postTime = race?.post_time;
  const raceDate = race?.race_date;
  const hasResults = race?.has_results ?? false;

  useEffect(() => {
    const timing = { post_time: postTime, race_date: raceDate, has_results: hasResults };
    if (!isUpcomingForecast(timing)) return;
    function refresh() {
      if (document.visibilityState === "hidden" || !isUpcomingForecast(timing)) return;
      for (const key of ["race", "predictions", "market-ev", "attention", "race-chaos", "odds"]) {
        void client.invalidateQueries({ queryKey: [key, raceId], refetchType: "active" });
      }
    }
    const timer = window.setInterval(refresh, pollMs);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [client, raceId, postTime, raceDate, hasResults, pollMs]);
}
