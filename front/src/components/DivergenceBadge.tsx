import type { HorsePrediction } from "../api/types";

// Feature 040 US3: NEUTRAL FACTUAL model-vs-market divergence badge.
// Pure comparison of model p vs market q — NO buy/sell/危険/妙味/edge semantics, NO directional
// sentiment ("弱気/強気" were rejected by codex R3 as too signal-like), NO profit colour, NO
// sorting by divergence. Null divergence => nothing rendered (suppressed when q missing or the
// p/q populations differ). The tooltip states this is an opinion difference, not a bet signal.

type Div = NonNullable<HorsePrediction["divergence"]>;

const LABELS: Record<Div, string> = {
  market_higher: "市場評価がモデルより高い",
  model_higher: "モデル評価が市場より高い",
  similar: "ほぼ同等",
};

const TOOLTIP =
  "モデル予測pと市場推定qの比較です。意見の相違であり、的中や利益を保証するものではありません";

export function DivergenceBadge({
  divergence,
  oddsAsOf,
}: {
  divergence: HorsePrediction["divergence"];
  oddsAsOf?: string | null;
}) {
  if (!divergence) return null; // suppressed
  // "similar" is shown muted so it does not draw attention as a signal.
  const cls = divergence === "similar" ? "badge badge-neutral" : "badge badge-divergence";
  const tip = oddsAsOf ? `${TOOLTIP}（市場q基準時点: ${oddsAsOf}）` : TOOLTIP;
  return (
    <span className={cls} title={tip}>
      {LABELS[divergence]}
    </span>
  );
}
