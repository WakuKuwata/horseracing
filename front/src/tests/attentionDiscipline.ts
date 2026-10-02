import { expect } from "vitest";

import { ROI_BASIS_LABEL_VALUES } from "../lib/attention";
import {
  ATTENTION_SCOPE,
  PROFIT_COLOUR_SELECTOR,
  UNMEASURED_ODDS_DRIFT,
} from "../lib/forbiddenPhrases";

/**
 * Feature 138 display discipline (FR-008 / SC-007), asserted on the DOM an attention component
 * draws — the chip elements and the Attention* containers ONLY. Never call this on the whole entries
 * table or page: the table's 乖離 title says 「…推奨ではありません」 and the page has a 「買い目推奨」
 * tab, which would trip `ATTENTION_SCOPE` (the 103 forbidden-phrase merge trap).
 *
 * - text matches neither `ATTENTION_SCOPE` nor `UNMEASURED_ODDS_DRIFT`
 * - no 印/推奨/おすすめ in any aria-label, no scoped phrase in any title
 * - no profit/loss colour class (`PROFIT_COLOUR_SELECTOR`, `.up`, `.down`)
 * - no win probability / p̂ (the panels speak in expected-return units only — constitution IV)
 */
export function assertAttentionDiscipline(roots: Element | Element[]): void {
  const list = Array.isArray(roots) ? roots : [roots];
  expect(list.length, "at least one attention root to check").toBeGreaterThan(0);
  for (const root of list) {
    const text = root.textContent ?? "";
    expect(text, "ATTENTION_SCOPE").not.toMatch(ATTENTION_SCOPE);
    expect(text, "UNMEASURED_ODDS_DRIFT").not.toMatch(UNMEASURED_ODDS_DRIFT);
    expect(text, "no win probability / p-hat").not.toMatch(/勝率|p̂/);
    const labelled = [root, ...Array.from(root.querySelectorAll("*"))];
    for (const el of labelled) {
      const aria = el.getAttribute("aria-label");
      if (aria !== null) {
        expect(aria, "aria-label").not.toMatch(/印|推奨|おすすめ/);
        expect(aria, "aria-label").not.toMatch(ATTENTION_SCOPE);
      }
      const title = el.getAttribute("title");
      if (title !== null) expect(title, "title").not.toMatch(ATTENTION_SCOPE);
    }
    const colour = `${PROFIT_COLOUR_SELECTOR}, .up, .down`;
    expect(root.matches(colour) || root.querySelector(colour) !== null, "P/L colour").toBe(false);
  }
}

/**
 * ROI label invariant (FR-008 / G3): every recovery-rate figure (`data-kind="roi"`) inside the
 * attention DOM carries exactly one of the frozen `ROI_BASIS_LABELS`. Expand collapsed sections
 * BEFORE calling (a closed panel would make this pass vacuously). Returns the number of figures so
 * the caller can assert the check was not vacuous.
 */
export function assertRoiBasisCoverage(root: Element): number {
  const figures = Array.from(root.querySelectorAll<HTMLElement>('[data-kind="roi"]'));
  for (const fig of figures) {
    const text = fig.textContent ?? "";
    const hits = ROI_BASIS_LABEL_VALUES.filter((label) => text.includes(label));
    expect(hits.length, `ROI figure "${text}" must carry exactly one basis label`).toBe(1);
  }
  return figures.length;
}
