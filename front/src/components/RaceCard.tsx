import { Link } from "react-router-dom";

import type { MarketEvDayItem, RaceSummary } from "../api/types";
import { formatJstDateTime, formatPostTime, PLACEHOLDER } from "../lib/format";

/** Race title: prefer the race name (e.g. ホープフルＳ); strip the JRA-VAN "*" suffix used for
 *  non-stakes rows (未勝利* → 未勝利). Fall back to race_class, then a placeholder. */
function raceTitle(race: RaceSummary): string {
  const name = race.race_name?.replace(/\*+$/, "").trim();
  return name || race.race_class || PLACEHOLDER;
}

/** JST "HH:MM" when `iso` falls on the race day (JST), else the full JST date-time. */
function computedLabel(iso: string | null, raceDate: string | null | undefined): string {
  const full = formatJstDateTime(iso);
  if (!full) return PLACEHOLDER;
  const [day, time] = full.split(" ");
  return raceDate && day === raceDate.replaceAll("-", "/") ? time : full;
}

/**
 * B7: the day listing's 期待回収率 hint for one race. `undefined` = the day listing is not known
 * (loading / failed) → nothing is drawn; `null` = known and this race has no stored value.
 * Counts and state only (021 規律): no per-horse value, no colour, no ordering by it. A price
 * move after the compute is only worth a word before the result (afterwards the stored odds are
 * the official final ones, so it is always true).
 */
export function marketEvHint(
  item: MarketEvDayItem | null | undefined,
  raceDate?: string | null,
): string | null {
  if (item === undefined) return null;
  if (item === null) return "期待回収率 未計算";
  if (item.status !== "available") {
    return item.reason === "field_changed"
      ? "期待回収率 再計算待ち(出走馬が変わりました)"
      : "期待回収率 単勝オッズ未取得";
  }
  const parts = [
    `期待回収率 ${computedLabel(item.computed_at, raceDate)} 計算`,
    `120%超 ${item.n_exceeds_threshold ?? 0}頭`,
    item.mix_status === "available"
      ? `混合100%超 ${item.n_exceeds_mix_threshold ?? 0}頭`
      : "混合なし",
  ];
  if (item.odds_changed_after_compute && !item.has_results) parts.push("計算後にオッズ変動");
  return parts.join(" · ");
}

/** A single race rendered as a clickable card: number, race title, class · course · distance. */
export function RaceCard({
  race,
  marketEv,
}: {
  race: RaceSummary;
  /** B7: this race's row of `/market-ev/day` (null = none; omit while the day is unknown). */
  marketEv?: MarketEvDayItem | null;
}) {
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
  const evHint = marketEvHint(marketEv, race.race_date);

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
      {evHint && (
        <span className="race-card__ev" aria-label="期待回収率の状態" data-testid="race-card-ev">
          {evHint}
        </span>
      )}
    </Link>
  );
}
