import type { ReactNode } from "react";

/**
 * SINGLE render path for any value that is pseudo / estimated / double-pseudo.
 *
 * CONSTITUTION V + 015 invariant: a pseudo value MUST NEVER reach the screen without a badge.
 * Every estimated odd, pseudo_odds, pseudo_roi, or double-pseudo figure renders through
 * <PseudoValue>, which stamps `data-pseudo="true"` and an adjacent <PseudoBadge>. The test helper
 * `assertPseudoLabelCoverage` asserts coverage by scanning for `data-pseudo` nodes — so this is the
 * ONLY component allowed to display such values. Do not format pseudo numbers inline elsewhere.
 */

export type PseudoKind = "estimated" | "pseudo" | "double_pseudo";

const LABELS: Record<PseudoKind, string> = {
  estimated: "推定",
  pseudo: "疑似",
  double_pseudo: "二重疑似",
};

const TITLES: Record<PseudoKind, string> = {
  estimated: "推定市場オッズ(PL外挿・実オッズではない)",
  pseudo: "疑似値(モデル確率の逆数・実績ではない)",
  double_pseudo: "二重疑似(推定オッズ + PL外挿・実現ROIではない)",
};

export function PseudoBadge({ kind }: { kind: PseudoKind }) {
  return (
    <span className="badge badge--pseudo" data-pseudo-badge={kind} title={TITLES[kind]}>
      {LABELS[kind]}
    </span>
  );
}

/** Wraps a value that is NOT real — always marks it and shows a badge. */
export function PseudoValue({
  kind,
  children,
}: {
  kind: PseudoKind;
  children: ReactNode;
}) {
  return (
    <span className="pseudo-value" data-pseudo="true" data-pseudo-kind={kind}>
      {children} <PseudoBadge kind={kind} />
    </span>
  );
}

/** Real/observed odds source badge (win=real, real_exotic=real). Distinct from pseudo. */
export function SourceBadge({
  source,
  coverageScope,
}: {
  source: "real" | "estimated";
  coverageScope?: string | null;
}) {
  if (source === "estimated") return <PseudoBadge kind="estimated" />;
  return (
    <span className="badge badge--real" data-source="real" title="実オッズ(netkeiba配当)">
      実{coverageScope ? `・${coverageScope}` : ""}
    </span>
  );
}
