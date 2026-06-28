import { useQuery } from "@tanstack/react-query";

import { api, parseApiError, type ErrorInfo } from "./client";
import type {
  OddsResponse,
  PredictionResponse,
  RaceDetail,
  RacePage,
  RecommendationResponse,
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
) {
  return useQuery<PredictionResponse, ErrorInfo>({
    queryKey: ["predictions", raceId, joint ?? null],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/races/{race_id}/predictions", {
          params: {
            path: { race_id: raceId },
            query: joint ? { bet_type: joint.bet_type, top: joint.top } : {},
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
