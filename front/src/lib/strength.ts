/**
 * Feature 087: display-only stake-strength tiers (厚め/標準/抑え) from the RACE-WIDE relative
 * stake fraction. Thresholds are frozen constants (never tuned against results — constitution
 * III), and boundary comparison uses an epsilon: naive `>` misclassifies exact thirds because
 * 0.1/0.3 = 0.33333333333333337 > 1/3 and 0.2/0.3 = 0.6666666666666667 > 2/3 in floats.
 */

export type StrengthLabel = "厚め" | "標準" | "抑え";

export interface Strength {
  label: StrengthLabel;
  ratio: number;
  barPct: number;
}

const EPS = 1e-9;

export function strengthOf(
  f: number | null | undefined,
  fMax: number,
): Strength | null {
  if (f === null || f === undefined || !Number.isFinite(f) || f < 0) return null;
  if (!Number.isFinite(fMax) || fMax <= 0) return null;
  const ratio = f / fMax;
  const label: StrengthLabel =
    ratio > 2 / 3 + EPS ? "厚め" : ratio > 1 / 3 + EPS ? "標準" : "抑え";
  const barPct = Math.min(100, Math.max(0, ratio * 100));
  return { label, ratio, barPct };
}

/**
 * The race-wide maximum: computed ONCE over every displayed row (all bet types) — per-group
 * recomputation is forbidden (it would crown every group's top row 厚め). Invalid fractions
 * are ignored; an all-null/non-positive race yields 0 → strengthOf returns null for all rows.
 */
export function raceMaxFraction(
  fractions: Array<number | null | undefined>,
): number {
  let max = 0;
  for (const f of fractions) {
    if (f !== null && f !== undefined && Number.isFinite(f) && f > max) max = f;
  }
  return max;
}
