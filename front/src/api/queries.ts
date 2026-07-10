import { useQuery } from "@tanstack/react-query";

import { api, parseApiError, type ErrorInfo } from "./client";
import type {
  CalibrationResponse,
  HorseHistoryPage,
  HorseProfile,
  ImportanceResponse,
  JockeyHistoryPage,
  JockeyProfile,
  OddsResponse,
  PredictionResponse,
  RaceDetail,
  RacePage,
  RecommendationResponse,
  ShadowLogResponse,
} from "./types";

// openapi-fetch returns { data, error, response }. We normalize the error branch into ErrorInfo
// and throw it so react-query's `error` is always typed as ErrorInfo (never `unknown`).
function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw parseApiError(result.response.status, result.error);
  }
  return result.data as T;
}

export function useRaces(
  params: {
    page?: number;
    page_size?: number;
    date?: string;
    venue?: string;
  },
  options?: { enabled?: boolean },
) {
  return useQuery<RacePage, ErrorInfo>({
    queryKey: ["races", params],
    enabled: options?.enabled ?? true,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/races", {
          params: { query: params },
        }),
      ),
  });
}

export function useRace(raceId: string) {
  return useQuery<RaceDetail, ErrorInfo>({
    queryKey: ["race", raceId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/races/{race_id}", {
          params: { path: { race_id: raceId } },
        }),
      ),
  });
}

export function usePredictions(
  raceId: string,
  joint?: { bet_type: string; top: number },
  modelVersion?: string,
) {
  return useQuery<PredictionResponse, ErrorInfo>({
    // Feature 057: modelVersion is part of the key so switching refetches. Omitted → active model.
    queryKey: ["predictions", raceId, joint ?? null, modelVersion ?? null],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/races/{race_id}/predictions", {
          params: {
            path: { race_id: raceId },
            query: {
              ...(joint ? { bet_type: joint.bet_type, top: joint.top } : {}),
              ...(modelVersion ? { model_version: modelVersion } : {}),
            },
          },
        }),
      ),
  });
}

export function useOdds(raceId: string, betType?: string) {
  return useQuery<OddsResponse, ErrorInfo>({
    queryKey: ["odds", raceId, betType ?? null],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/races/{race_id}/odds", {
          params: {
            path: { race_id: raceId },
            query: betType ? { bet_type: betType } : {},
          },
        }),
      ),
  });
}

export function useCalibration(modelVersion: string | undefined, label = "win") {
  // Feature 021 US2: read-only walk-forward OOS reliability for a model_version. Disabled until the
  // model_version is known (it comes from the predictions run audit).
  return useQuery<CalibrationResponse, ErrorInfo>({
    queryKey: ["calibration", modelVersion, label],
    enabled: !!modelVersion,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/models/{model_version}/calibration", {
          params: { path: { model_version: modelVersion as string }, query: { label } },
        }),
      ),
  });
}

export function useImportance(modelVersion: string | undefined) {
  // Feature 040 US2: read-only split-gain (gain) importance for a model_version. Disabled until
  // the model_version is known (it comes from the predictions run audit).
  return useQuery<ImportanceResponse, ErrorInfo>({
    queryKey: ["importance", modelVersion],
    enabled: !!modelVersion,
    // A typed 404 (importance_unavailable) is deterministic — retrying won't make it appear, and
    // retry backoff would leave the panel stuck on "loading". Settle immediately to the state.
    retry: false,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/models/{model_version}/importance", {
          params: { path: { model_version: modelVersion as string } },
        }),
      ),
  });
}

export function useRecommendations(raceId: string) {
  // The 014 API returns ALL persisted recommendation rows for the race (no query filter);
  // bet-type filtering is done client-side so the read-only contract stays untouched.
  return useQuery<RecommendationResponse, ErrorInfo>({
    queryKey: ["recommendations", raceId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/races/{race_id}/recommendations", {
          params: { path: { race_id: raceId } },
        }),
      ),
  });
}

// --- Feature 065: prospective shadow-betting log (honest instrument) ---------
export function useShadowLog() {
  return useQuery<ShadowLogResponse, ErrorInfo>({
    queryKey: ["shadow-log"],
    queryFn: async () => unwrap(await api.GET("/api/v1/shadow-log", {})),
  });
}

// --- Feature 029: horse profile (facts, not model features) -----------------
export function useHorseProfile(horseId: string) {
  return useQuery<HorseProfile, ErrorInfo>({
    queryKey: ["horse", horseId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/horses/{horse_id}", {
          params: { path: { horse_id: horseId } },
        }),
      ),
  });
}

export function useHorseHistory(
  horseId: string,
  params: { page?: number; page_size?: number } = {},
) {
  return useQuery<HorseHistoryPage, ErrorInfo>({
    queryKey: ["horse-history", horseId, params],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/horses/{horse_id}/history", {
          params: { path: { horse_id: horseId }, query: params },
        }),
      ),
  });
}

export function useJockeyProfile(jockeyId: string) {
  return useQuery<JockeyProfile, ErrorInfo>({
    queryKey: ["jockey", jockeyId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/jockeys/{jockey_id}", {
          params: { path: { jockey_id: jockeyId } },
        }),
      ),
  });
}

export function useJockeyHistory(
  jockeyId: string,
  params: { page?: number; page_size?: number } = {},
) {
  return useQuery<JockeyHistoryPage, ErrorInfo>({
    queryKey: ["jockey-history", jockeyId, params],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/jockeys/{jockey_id}/history", {
          params: { path: { jockey_id: jockeyId }, query: params },
        }),
      ),
  });
}
