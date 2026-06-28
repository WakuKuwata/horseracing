import { useMemo } from "react";

import type { RaceSummary } from "../api/types";
import { venueName } from "../lib/venues";
import { RaceCard } from "./RaceCard";

type VenueGroup = { code: string; races: RaceSummary[] };

/** Group a single day's races by venue (sorted by venue code), races sorted by race number. */
function groupByVenue(races: RaceSummary[]): VenueGroup[] {
  const byCode = new Map<string, RaceSummary[]>();
  for (const r of races) {
    const code = r.venue_code ?? "—";
    const list = byCode.get(code) ?? [];
    list.push(r);
    byCode.set(code, list);
  }
  return [...byCode.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([code, list]) => ({
      code,
      // explicit sort: do not rely on the API's incidental ordering inside a venue
      races: [...list].sort(
        (a, b) => (a.race_number ?? 0) - (b.race_number ?? 0),
      ),
    }));
}

export function RaceDayBoard({ races }: { races: RaceSummary[] }) {
  const groups = useMemo(() => groupByVenue(races), [races]);

  return (
    <div className="day-board">
      {groups.map((g) => (
        <section key={g.code} className="venue-group">
          <h2 className="venue-group__title">
            {venueName(g.code)}
            <span className="venue-group__count">{g.races.length}R</span>
          </h2>
          <div className="venue-group__cards">
            {g.races.map((r) => (
              <RaceCard key={r.race_id} race={r} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
