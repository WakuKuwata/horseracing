import { expect } from "vitest";

/**
 * Coverage assertion (NOT a spot-check) for the 015 pseudo-label invariant.
 *
 * Given the rendered container and the list of values that ARE pseudo/estimated, assert:
 *  1. every such value renders inside a `[data-pseudo="true"]` node (i.e. via <PseudoValue>), and
 *  2. every `[data-pseudo]` node carries a `[data-pseudo-badge]` (the visible 推定/疑似/二重疑似 chip).
 *
 * codex flagged spot-checking individual fields as insufficient — this walks the whole subtree so a
 * newly-added pseudo field that forgets the badge will fail the test.
 */
export function assertPseudoLabelCoverage(
  container: HTMLElement,
  pseudoTexts: string[],
): void {
  const pseudoNodes = Array.from(
    container.querySelectorAll<HTMLElement>('[data-pseudo="true"]'),
  );

  for (const text of pseudoTexts) {
    const covered = pseudoNodes.some((n) => n.textContent?.includes(text));
    expect(
      covered,
      `pseudo value "${text}" must render inside a [data-pseudo] node (use <PseudoValue>)`,
    ).toBe(true);
  }

  for (const node of pseudoNodes) {
    expect(
      node.querySelector("[data-pseudo-badge]"),
      "every [data-pseudo] node must carry a <PseudoBadge>",
    ).not.toBeNull();
  }
}
