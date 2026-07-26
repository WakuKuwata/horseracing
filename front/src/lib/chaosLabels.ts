/**
 * Feature 084: single source of truth for the top-3 chaos readout's Japanese labels.
 *
 * This vocabulary is intentionally distinct from Feature 066's firm..open scale. All wording is
 * descriptive of market-derived probabilities, not advice or a claim of an informational edge.
 */
export type ChaosBand =
  | "t3_calm"
  | "t3_mild"
  | "t3_mid"
  | "t3_rough"
  | "t3_wild";

export type ChaosEventKey = "s_ge_20" | "himo_are" | "total_collapse";

export const SCALE_NAME = "上位3着の荒れ度";

export const BAND_LABEL: Record<ChaosBand, string> = {
  t3_calm: "揃う",
  t3_mild: "やや揃う",
  t3_mid: "標準",
  t3_rough: "やや崩れる",
  t3_wild: "崩れやすい",
};

/** Ascending chaos order. Index = gauge level 0..4. */
export const BAND_ORDER: ChaosBand[] = [
  "t3_calm",
  "t3_mild",
  "t3_mid",
  "t3_rough",
  "t3_wild",
];

/** Labels are logically equivalent to the preregistered event predicates. */
export const EVENT_LABEL: Record<ChaosEventKey, string> = {
  s_ge_20: "人気順合計が20以上",
  himo_are: "1〜3番人気が勝ち、2着か3着に二桁人気",
  total_collapse: "二桁人気が勝つ",
};

export const PRE_CONFIRMATION_WORDING =
  "参考値(最終オッズでは検証済み／発走前オッズで検証中)";

export const STANDING_DISCLOSURE =
  "市場オッズを上位3着構成へ変換した参考値です。市場にない独自情報や収益上の優位性を示しません。";

export const TOTAL_COLLAPSE_NOTE =
  "ステージ割引補正は適用されません（生の市場質量です）。";

export const STRUCTURAL_ZERO_LABEL = "該当馬なし";
