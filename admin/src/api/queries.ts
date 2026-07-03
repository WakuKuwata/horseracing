import { useQuery } from "@tanstack/react-query";

import { api, parseApiError, type ErrorInfo } from "./client";
import type {
  CalibrationResponse,
  CoverageResponse,
  ImportanceResponse,
  JobListResponse,
  ModelListResponse,
} from "./types";

// openapi-fetch returns { data, error, response }. Normalize the error branch into ErrorInfo and
// throw so react-query's `error` is always typed (same unwrap pattern as front).
function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw parseApiError(result.response.status, result.error);
  }
  return result.data as T;
}

export function useModels() {
  return useQuery<ModelListResponse, ErrorInfo>({
    queryKey: ["models"],
    queryFn: async () => unwrap(await api.GET("/api/v1/models")),
  });
}

export function useCalibration(modelVersion: string, label: "win" | "top2" | "top3" = "win") {
  return useQuery<CalibrationResponse, ErrorInfo>({
    queryKey: ["calibration", modelVersion, label],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/models/{model_version}/calibration", {
          params: { path: { model_version: modelVersion }, query: { label } },
        }),
      ),
  });
}

export function useImportance(modelVersion: string) {
  return useQuery<ImportanceResponse, ErrorInfo>({
    queryKey: ["importance", modelVersion],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/models/{model_version}/importance", {
          params: { path: { model_version: modelVersion } },
        }),
      ),
  });
}

export function useCoverage(dateFrom: string, dateTo: string) {
  return useQuery<CoverageResponse, ErrorInfo>({
    queryKey: ["coverage", dateFrom, dateTo],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/coverage", {
          params: { query: { date_from: dateFrom, date_to: dateTo } },
        }),
      ),
  });
}

export function useJobs(filters: { status?: string; job_type?: string; limit?: number }) {
  return useQuery<JobListResponse, ErrorInfo>({
    queryKey: ["jobs", filters],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/jobs", { params: { query: filters } })),
  });
}
