// Central formatting for nullable numbers. The API returns many `number | null` fields
// (odds, pseudo_roi, win_prob); rendering raw null/undefined is forbidden — always route through here.

const EM_DASH = "—";

/** Format a possibly-null number with fixed decimals, or an em-dash placeholder when absent. */
export function formatNum(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH;
  return value.toFixed(digits);
}

/** Format a probability (0..1) as a percentage, or em-dash when absent. */
export function formatPct(
  value: number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH;
  return `${(value * 100).toFixed(digits)}%`;
}

/** Format odds (×N.N), or em-dash when absent. */
export function formatOdds(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH;
  return `×${value.toFixed(1)}`;
}

/** ISO datetime → locale-ish short string; em-dash when absent. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return EM_DASH;
  return value.replace("T", " ").replace(/\.\d+/, "").replace("Z", " UTC");
}

/** ISO datetime → human-facing "YYYY/MM/DD HH:MM" in Asia/Tokyo (the race-time zone), or null
 *  when absent/unparseable. formatDateTime above is the UTC audit form; this is what people read
 *  next to race times (OddsFreshness, Feature 137 期待回収率 note). */
export function formatJstDateTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Tokyo",
  }).format(date);
}

/** Post time (JST-aware ISO) → "12:55" in Asia/Tokyo; em-dash when absent. netkeiba-sourced, so
 *  JRA-VAN-only races have none — callers may omit the chip rather than render the placeholder. */
export function formatPostTime(value: string | null | undefined): string {
  if (!value) return EM_DASH;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return EM_DASH;
  return new Intl.DateTimeFormat("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Tokyo",
  }).format(d);
}

/** A bet-type/horse-number selection array → "1-2-3". */
export function formatSelection(selection: number[] | null | undefined): string {
  if (!selection || selection.length === 0) return EM_DASH;
  return selection.join("-");
}

export const PLACEHOLDER = EM_DASH;
