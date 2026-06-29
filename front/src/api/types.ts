import type { components } from "./schema";

type S = components["schemas"];

export type RaceSummary = S["RaceSummary"];
export type RaceDetail = S["RaceDetail"];
export type HorseEntry = S["HorseEntry"];
export type PredictionResponse = S["PredictionResponse"];
export type HorsePrediction = S["HorsePrediction"];
export type JointEntry = S["JointEntry"];
export type RunAudit = S["RunAudit"];
export type OddsResponse = S["OddsResponse"];
export type WinOddsRow = S["WinOddsRow"];
export type EstimatedOddsRow = S["EstimatedOddsRow"];
export type RealExoticOddsRow = S["RealExoticOddsRow"];
export type RecommendationResponse = S["RecommendationResponse"];
export type RecommendationRow = S["RecommendationRow"];
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
