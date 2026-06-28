import { Link } from "react-router-dom";

import type { RaceSummary } from "../api/types";
import { formatPostTime, PLACEHOLDER } from "../lib/format";

/** Race title: prefer the race name (e.g. ホープフルＳ); strip the JRA-VAN "*" suffix used for
 *  non-stakes rows (未勝利* → 未勝利). Fall back to race_class, then a placeholder. */
function raceTitle(race: RaceSummary): string {
  const name = race.race_name?.replace(/\*+$/, "").trim();
  return name || race.race_class || PLACEHOLDER;
}

/** A single race rendered as a clickable card: number, race title, class · course · distance. */
export function RaceCard({ race }: { race: RaceSummary }) {
  const meta = [
    race.race_class,
    race.track_type,
    race.distance != null ? `${race.distance}m` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const confirmed = race.has_results === true;
  // 発走時刻 (netkeiba-sourced). JRA-VAN-only races have none → omit the chip rather than show a
  // placeholder for every historical race.
  const postTime = race.post_time ? formatPostTime(race.post_time) : null;

  return (
    <Link to={`/races/${race.race_id}`} className="race-card">
      <span className="race-card__no">{race.race_number ?? PLACEHOLDER}R</span>
      <span className="race-card__body">
        <span className="race-card__title">{raceTitle(race)}</span>
        <span className="race-card__meta">{meta || PLACEHOLDER}</span>
      </span>
      {postTime && (
        <span className="race-card__post" aria-label="発走時刻">
          発走 {postTime}
        </span>
      )}
      <span
        className={`race-status race-status--${confirmed ? "confirmed" : "pending"}`}
      >
        {confirmed ? "結果確定" : "結果待ち"}
      </span>
    </Link>
  );
}
