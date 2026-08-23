/**
 * Local-calendar date helpers.
 *
 * The obvious `new Date().toISOString().slice(0, 10)` is wrong for any timezone east of UTC: the
 * mutation (`setDate`) happens in LOCAL time but `toISOString` converts to UTC first, so in JST
 * every morning before 09:00 the result is the previous day. That silently shifted the coverage
 * page's default range — `isoDaysAgo(0)` returned 2026-08-22 at 2026-08-23 08:00 JST, excluding
 * today from both the table and the range-refresh button.
 */

/** ISO `YYYY-MM-DD` for "N days ago", read from the LOCAL calendar fields. */
export function isoDaysAgo(days: number, now: Date = new Date()): string {
  const d = new Date(now.getTime());
  d.setDate(d.getDate() - days);
  return toLocalIsoDate(d);
}

/** ISO `YYYY-MM-DD` for a Date, using its local (not UTC) calendar fields. */
export function toLocalIsoDate(d: Date): string {
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}
