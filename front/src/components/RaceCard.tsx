import { Link } from "react-router-dom";

import type { RaceSummary } from "../api/types";
import { PLACEHOLDER } from "../lib/format";

/** A single race rendered as a clickable card (race-number, class, course, distance). */
export function RaceCard({ race }: { race: RaceSummary }) {
  return (
    <Link to={`/races/${race.race_id}`} className="race-card">
      <span className="race-card__no">{race.race_number ?? PLACEHOLDER}R</span>
      <span className="race-card__body">
        <span className="race-card__class">{race.race_class ?? PLACEHOLDER}</span>
        <span className="race-card__meta">
          {race.track_type ?? PLACEHOLDER}
          {race.distance != null ? ` ${race.distance}m` : ""}
        </span>
      </span>
    </Link>
  );
}
