import { expect } from "vitest";

import { assertAttentionDiscipline, assertRoiBasisCoverage } from "./attentionDiscipline";

/**
 * Feature 138: stricter checks layered on the shared `attentionDiscipline` helpers for the list
 * components (rules list, day list). Same scope rule: ONE attention component's DOM only, never the
 * whole table/page (existing 「買い目推奨」 wording elsewhere would trip `ATTENTION_SCOPE`).
 */

/** Shared discipline + no 「印」(bare mark word) and no 「通常」 anywhere in the scope's text
 *  (the freshness cut-offs are promises about elapsed time, not measured odds drift). */
export function assertListDiscipline(scope: HTMLElement): void {
  assertAttentionDiscipline(scope);
  expect(scope.textContent ?? "").not.toMatch(/印|推奨|おすすめ|通常/);
}

/**
 * ROI label invariant (shared `assertRoiBasisCoverage`) + no percentage escapes the labelled nodes:
 * every text node with a `N%` sits inside an element that says what the number is (`data-kind`:
 * roi / expected_return / definition / criterion / sigma / overlap). A stray ROI typed inline
 * without the roi node (and so without a basis label) fails here. Open any <details> first.
 * Returns the roi nodes so callers can also assert coverage of specific values.
 */
export function assertRoiBasisLabels(scope: HTMLElement): HTMLElement[] {
  expect(assertRoiBasisCoverage(scope), "non-vacuous: at least one ROI figure").toBeGreaterThan(0);
  const walker = scope.ownerDocument.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!/\d\s*%/.test(n.textContent ?? "")) continue;
    expect(
      n.parentElement?.closest("[data-kind]"),
      `percentage "${n.textContent}" must sit inside a [data-kind] node (roi needs a basis label)`,
    ).not.toBeNull();
  }
  return Array.from(scope.querySelectorAll<HTMLElement>('[data-kind="roi"]'));
}

/** True when some roi node shows `value` (already formatted, e.g. "121.1%") with `label`. */
export function hasRoiWithLabel(roiNodes: HTMLElement[], value: string, label: string): boolean {
  return roiNodes.some((n) => {
    const text = n.textContent ?? "";
    return text.includes(value) && text.includes(`〔${label}〕`);
  });
}
