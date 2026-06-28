import type { HorsePrediction } from "../api/types";

/**
 * Feature 021 US3: NEUTRAL prior-start-volume indicator ("出走歴 少/中/多").
 *
 * codex steer (R6 + T016 thin margin): this is NOT a confidence/calibration signal — the validated
 * weak−strong ECE gap (+0.00211) cleared the gate by only +0.00011, too thin to claim "this
 * probability is less reliable". So it ships as a plain FACT (how much race history backs the horse):
 * factual labels (少/中/多, not weak/strong), no win/loss colour, no sorting, and the title says it
 * is not a prediction-accuracy guarantee. Derived from prior-start count only (pre-race, leak-safe).
 */
type Band = NonNullable<HorsePrediction["prior_starts_band"]>;

const LABEL: Record<Band, string> = {
  few: "出走歴 少",
  some: "出走歴 中",
  many: "出走歴 多",
};

const TITLE = "過去出走数の目安(少≤1 / 中2-5 / 多≥6)。予測の的中確信・精度の保証ではありません。";

export function DataBackingBadge({
  band,
}: {
  band: HorsePrediction["prior_starts_band"];
}) {
  if (!band) return <>—</>;
  return (
    <span className="badge badge--history" data-prior-starts-band={band} title={TITLE}>
      {LABEL[band]}
    </span>
  );
}
