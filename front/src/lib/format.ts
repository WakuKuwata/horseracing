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

/** A bet-type/horse-number selection array → "1-2-3". */
export function formatSelection(selection: number[] | null | undefined): string {
  if (!selection || selection.length === 0) return EM_DASH;
  return selection.join("-");
}

export const PLACEHOLDER = EM_DASH;
