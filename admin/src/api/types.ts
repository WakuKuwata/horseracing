// Convenience re-exports over the generated OpenAPI schema (same pattern as front/src/api/types.ts).
// schema.d.ts is auto-generated from the committed admin/openapi.json — do not edit by hand.
import type { components } from "./schema";

type S = components["schemas"];

export type ModelVersionRow = S["ModelVersionRow"];
export type ModelListResponse = S["ModelListResponse"];
export type CalibrationResponse = S["CalibrationResponse"];
export type CalibrationBin = S["CalibrationBin"];
export type ImportanceResponse = S["ImportanceResponse"];

export type CoverageResponse = S["CoverageResponse"];
export type CoverageDay = S["CoverageDay"];
export type JobListResponse = S["JobListResponse"];
export type JobRow = S["JobRow"];
