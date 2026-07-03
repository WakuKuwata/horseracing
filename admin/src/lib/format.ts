// Central nullable formatting (same discipline as front/src/lib/format.ts): the API returns many
// `number | null` fields — never render raw null/NaN, always route through here.

const EM_DASH = "—";

export function formatNum(value: number | null | undefined, digits = 5): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH;
  return value.toFixed(digits);
}

export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH;
  return value.toLocaleString("ja-JP");
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return EM_DASH;
  return value.replace("T", " ").replace(/\.\d+/, "").replace("Z", " UTC");
}

export function textOr(value: string | null | undefined): string {
  return value ?? EM_DASH;
}

export const PLACEHOLDER = EM_DASH;
