import { useCallback, useState } from "react";

/**
 * Feature 087: race budget → yen conversion for the betting slip.
 *
 * All money arithmetic lives here so the display layer cannot invent amounts (FR-043):
 * the ONLY allowed derivation is stake_fraction × budget floored to the ¥100 JRA unit.
 * The budget itself is browser-local only (localStorage) and never sent to the server.
 */

export type AmountVM =
  | { kind: "reference" }
  | { kind: "too_small" }
  | { kind: "amount"; yen: number };

export const BUDGET_STORAGE_KEY = "horseracing.race_budget.v1";

const YEN_UNIT = 100;

/**
 * Budget acceptance (single source of truth, display contract §1): a positive safe-integer
 * yen amount of at least ¥100. Multiples of 100 are NOT enforced (¥550 is valid — the
 * per-bet floor absorbs it). Decimals/NaN/Infinity/garbage strings are rejected as null.
 */
export function validateBudget(input: unknown): number | null {
  let n: unknown = input;
  if (typeof input === "string") {
    if (input.trim() === "" || !/^\d+$/.test(input.trim())) return null;
    n = Number(input.trim());
  }
  if (typeof n !== "number" || !Number.isFinite(n) || !Number.isSafeInteger(n)) return null;
  if (n < YEN_UNIT) return null;
  return n;
}

/**
 * floor(f×b) to the ¥100 unit with a boundary snap: float error can silently lose a whole
 * unit (0.036×25000 = 899.9999… → ¥800 instead of ¥900), so a raw value within a few ULP
 * of an exact ¥100 multiple snaps to it before flooring. A genuine ¥899.9 is NOT snapped.
 */
export function computeAmount(
  stakeFraction: number | null | undefined,
  budget: number,
): AmountVM {
  if (stakeFraction === null || stakeFraction === undefined) return { kind: "reference" };
  const raw = stakeFraction * budget;
  const nearest = Math.round(raw / YEN_UNIT) * YEN_UNIT;
  const tolerance = Math.max(1, Math.abs(raw)) * Number.EPSILON * 64;
  const yen =
    Math.abs(raw - nearest) < tolerance ? nearest : Math.floor(raw / YEN_UNIT) * YEN_UNIT;
  if (yen < YEN_UNIT) return { kind: "too_small" };
  return { kind: "amount", yen };
}

export interface SlipSummary {
  totalYen: number;
  count: number;
  budgetPct: number;
  overBudget: boolean;
}

/** Sum of the DISPLAYED convertible amounts only (reference/too_small rows never count). */
export function summarizeAmounts(amounts: AmountVM[], budget: number): SlipSummary {
  let totalYen = 0;
  let count = 0;
  for (const a of amounts) {
    if (a.kind === "amount") {
      totalYen += a.yen;
      count += 1;
    }
  }
  return {
    totalYen,
    count,
    budgetPct: Math.round((totalYen / budget) * 100),
    overBudget: totalYen > budget,
  };
}

const yenFormat = new Intl.NumberFormat("ja-JP");

/** Single JPY formatting path — mixing ¥/円 spellings is banned by the display contract. */
export function formatYen(n: number): string {
  return `¥${yenFormat.format(n)}`;
}

// --- persistence ------------------------------------------------------------------------------
// localStorage plus a module-level session mirror: when storage is unavailable (private mode,
// SecurityError), the value must still survive unmount→remount within this tab's session.
// Reads/writes are both guarded; renders never write (StrictMode double-evaluation safe).

let memoryBudget: number | null = null;

/** Test-only: reset the module-level session mirror between tests. */
export function resetBudgetMemoryForTests(): void {
  memoryBudget = null;
}

function readStoredBudget(): number | null {
  try {
    const raw = window.localStorage.getItem(BUDGET_STORAGE_KEY);
    if (raw !== null) {
      const valid = validateBudget(raw);
      if (valid !== null) return valid;
    }
  } catch {
    // storage unreadable — fall through to the session mirror
  }
  return memoryBudget;
}

export interface BudgetState {
  budget: number | null;
  setBudget: (value: number | null) => void;
}

/**
 * Single-owner hook: RecommendationPanel calls this exactly once and passes the value down
 * as props (BudgetInput is controlled; BetSlip receives the same value). Independent hook
 * calls in sibling components would not stay in sync within a tab and are forbidden.
 * Cross-tab live sync (`storage` events) is explicitly a non-requirement for this feature.
 */
export function useBudget(): BudgetState {
  const [budget, setState] = useState<number | null>(readStoredBudget);
  const setBudget = useCallback((value: number | null) => {
    memoryBudget = value;
    setState(value);
    try {
      if (value === null) window.localStorage.removeItem(BUDGET_STORAGE_KEY);
      else window.localStorage.setItem(BUDGET_STORAGE_KEY, String(value));
    } catch {
      // storage unwritable — the session mirror above keeps the value alive
    }
  }, []);
  return { budget, setBudget };
}
