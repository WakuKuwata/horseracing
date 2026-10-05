import type { components } from "./schema";

type S = components["schemas"];

export type RaceSummary = S["RaceSummary"];
export type RaceDetail = S["RaceDetail"];
export type HorseEntry = S["HorseEntry"];
export type PredictionResponse = S["PredictionResponse"];
export type HorsePrediction = S["HorsePrediction"];
// Feature 057: a selectable model for the race-detail model switcher
export type AvailableModel = S["AvailableModel"];
export type JointEntry = S["JointEntry"];
// Feature 066: race-level dispersion (荒れ度) + p/q divergence readout
export type RaceDispersion = S["RaceDispersion"];
export type RaceDivergence = S["RaceDivergence"];
export type RunAudit = S["RunAudit"];
export type OddsResponse = S["OddsResponse"];
export type WinOddsRow = S["WinOddsRow"];
export type EstimatedOddsRow = S["EstimatedOddsRow"];
export type RealExoticOddsRow = S["RealExoticOddsRow"];
export type RecommendationResponse = S["RecommendationResponse"];
export type RecommendationRow = S["RecommendationRow"];
export type FavoriteBaseline = S["FavoriteBaseline"];
export type ShadowLogResponse = S["ShadowLogResponse"];
export type RacePage = S["Page_RaceSummary_"];
export type CalibrationResponse = S["CalibrationResponse"];
export type CalibrationBin = S["CalibrationBin"];
// Feature 029: horse/jockey profiles
export type HorseProfile = S["HorseProfile"];
export type HorseHistoryRow = S["HorseHistoryRow"];
export type HorseHistoryPage = S["Page_HorseHistoryRow_"];
export type JockeyProfile = S["JockeyProfile"];
export type JockeyHistoryRow = S["JockeyHistoryRow"];
export type JockeyHistoryPage = S["Page_JockeyHistoryRow_"];
// Feature 040: prediction explanation, importance, divergence
export type Explanation = S["Explanation"];
export type ExplanationItem = S["ExplanationItem"];
export type ImportanceResponse = S["ImportanceResponse"];
export type ImportanceValue = S["ImportanceValue"];
// Feature 137: market-aware expected return (期待回収率) — a SEPARATE model from the win model
export type HorseMarketEv = S["HorseMarketEv"];
export type MarketEvAvailable = S["MarketEvAvailable"];
export type MarketEvUnavailable = S["MarketEvUnavailable"];
export type MarketEvResponse = MarketEvAvailable | MarketEvUnavailable;

// Feature 138: 注目条件 (attention conditions S1–S5). Judgment-time chips + frozen backtest +
// prospective tally. Stages are ASCII on the wire; Japanese lives only in lib/attention.ts.
export type AttentionAvailable = S["AttentionAvailable"];
export type AttentionUnavailable = S["AttentionUnavailable"];
export type AttentionResponse = AttentionAvailable | AttentionUnavailable;
export type AttentionHorse = S["AttentionHorse"];
export type EvSnapshot = S["EvSnapshot"];
export type StageDetail = S["StageDetail"];
export type AttentionLevels = S["AttentionLevels"];
export type AttentionRulesResponse = S["AttentionRulesResponse"];
export type RuleSummary = S["RuleSummary"];
export type AttentionBacktest = S["AttentionBacktest"];
export type AttentionFrozenStats = S["AttentionFrozenStats"];
export type AttentionSelectedCalibration = S["AttentionSelectedCalibration"];
export type AttentionPriceNoise = S["AttentionPriceNoise"];
export type AttentionRuleLevels = S["AttentionRuleLevels"];
export type AttentionProspective = S["AttentionProspective"];
// Feature 139: the prospective tally is settled at the official win payout (policy v2, the stage
// basis); the judged-odds settlement (v1) stays alongside as a reference.
export type AttentionOfficialBasis = S["AttentionOfficialBasis"];
export type AttentionFrozenBasis = S["AttentionFrozenBasis"];
export type AttentionStoredBasis = S["AttentionStoredBasis"];
export type AttentionExclusionCounts = S["AttentionExclusionCounts"];
export type AttentionFlags = S["AttentionFlags"];
export type AttentionJudgedFreshness = S["AttentionJudgedFreshness"];
export type AttentionFreshnessBand = S["AttentionFreshnessBand"];
export type AttentionOddsDrift = S["AttentionOddsDrift"];
export type CheckpointDecision = S["CheckpointDecision"];
// Feature 139: the registry's frozen buy-time conversion (過去データからの換算・参考値) and its source.
export type AttentionBuyTimeExpectation = S["AttentionBuyTimeExpectation"];
export type AttentionBuyTimeSource = S["AttentionBuyTimeSource"];
export type AttentionDayResponse = S["AttentionDayResponse"];
export type AttentionDayItem = S["AttentionDayItem"];
/** "S1".."S5" (S5 = control: never a chip). */
export type RuleId = RuleSummary["id"];
/** The chip rule re-checked against the current values: matches / no_longer / unknown. */
export type ChipNow = NonNullable<AttentionHorse["chip_now"]>;
export type Stage = StageDetail["stage"];
/** One axis level (過去検証・前向き検証・価格ずれ試験・価格鮮度): 1 (weakest) … 3. */
export type AxisLevel = AttentionLevels["backtest"];

// Feature 106: purchase records & triple comparison (read-only api side)
export type PurchaseRecordsResponse =
  components["schemas"]["PurchaseRecordsResponse"];
export type PurchaseRecordView = components["schemas"]["PurchaseRecordView"];
export type PurchaseBetView = components["schemas"]["PurchaseBetView"];
export type PurchaseComparisonResponse =
  components["schemas"]["PurchaseComparisonResponse"];
export type ComparisonPoint = components["schemas"]["ComparisonPoint"];
