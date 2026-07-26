import { render, screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { PredictionResponse } from "../api/types";
import {
  EVENT_LABEL,
  PRE_CONFIRMATION_WORDING,
  STANDING_DISCLOSURE,
  STRUCTURAL_ZERO_LABEL,
  TOTAL_COLLAPSE_NOTE,
} from "../lib/chaosLabels";
import { RaceDetailPage } from "../pages/RaceDetailPage";
import {
  happyHandlers,
  http,
  HttpResponse,
  predictionResponse,
} from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { RaceChaosPanel } from "./RaceChaosPanel";

type RaceChaos = NonNullable<PredictionResponse["race_chaos"]>;
type AvailableChaos = Extract<RaceChaos, { status: "available" }>;

const AVAILABLE: AvailableChaos = {
  status: "available",
  unavailable_reason: null,
  band: "t3_rough",
  band_axis: "p_s_ge_20",
  field_size: 10,
  feasible_support: [6, 27],
  feasible_support_ja: "人気合計は 6〜27 の範囲",
  events: [
    {
      key: "total_collapse",
      label_ja: "API側の文言を表示に使わない",
      adjusted_mass: 0.034,
      raw_mass: 0.034,
      is_structural_zero: false,
      structural_zero_reason: null,
      lambda_sensitive: false,
    },
    {
      key: "s_ge_20",
      label_ja: "API側の文言を表示に使わない",
      adjusted_mass: 0.126,
      raw_mass: 0.081,
      is_structural_zero: false,
      structural_zero_reason: null,
      lambda_sensitive: true,
    },
    {
      key: "himo_are",
      label_ja: "API側の文言を表示に使わない",
      adjusted_mass: 0.234,
      raw_mass: 0.202,
      is_structural_zero: false,
      structural_zero_reason: null,
      lambda_sensitive: true,
    },
  ],
  expected_top3_popularity_sum: 12.75,
  within_field_size_percentile: 65,
  calibration_status: "provisional",
  calibration_basis: "closing_history_2020_2023",
  is_market_derived: true,
  is_pseudo: true,
  snapshot: {
    captured_at: "2026-07-26T04:00:00Z",
    source: "netkeiba",
    seconds_to_post: 1800,
    capture_strength: "confirmatory",
    content_digest: "c".repeat(64),
    snapshot_id: "00000000-0000-4000-8000-000000000084",
  },
  artifact_version: "chaosbands-v1",
  artifact_digest: "a".repeat(64),
  readout_source: "persisted",
  persisted_artifact_digest: "a".repeat(64),
};

describe("RaceChaosPanel", () => {
  it("leads with P(S>=20), then shows its separate five-step scale and secondary E[S]", () => {
    render(<RaceChaosPanel chaos={AVAILABLE} />);

    expect(screen.getByTestId("chaos-primary")).toHaveTextContent("13%");
    expect(screen.getByTestId("chaos-main")).toHaveTextContent(EVENT_LABEL.s_ge_20);
    expect(screen.getByTestId("chaos-band")).toHaveTextContent("やや崩れる");
    expect(screen.getByText(/P\(人気順合計≥20\) の5段階スケール/)).toBeInTheDocument();
    expect(screen.getByText("12.8")).toBeInTheDocument();
  });

  it("uses predicate-equivalent event labels and explains the raw total-collapse mass", () => {
    const { container } = render(<RaceChaosPanel chaos={AVAILABLE} />);

    expect(screen.getAllByText(EVENT_LABEL.himo_are).length).toBeGreaterThan(0);
    expect(screen.getAllByText(EVENT_LABEL.total_collapse).length).toBeGreaterThan(0);
    expect(container.textContent).not.toContain("二桁人気が2・3着");
    expect(container.textContent).not.toContain("内訳");
    expect(screen.getByText(TOTAL_COLLAPSE_NOTE)).toBeInTheDocument();
  });

  it("uses the pre-confirmation wording and always shows the standing disclosure", () => {
    render(<RaceChaosPanel chaos={AVAILABLE} />);
    expect(screen.getByTestId("chaos-pre-confirmation")).toHaveTextContent(
      PRE_CONFIRMATION_WORDING,
    );
    expect(screen.getByTestId("chaos-standing-disclosure")).toHaveTextContent(
      STANDING_DISCLOSURE,
    );
  });

  it("keeps raw masses in a collapsed method disclosure, away from the main readout", () => {
    render(<RaceChaosPanel chaos={AVAILABLE} />);

    const method = screen.getByTestId("chaos-method");
    expect(method).not.toHaveAttribute("open");
    expect(within(method).getByText("8%")).toBeInTheDocument();
    expect(within(screen.getByTestId("chaos-main")).queryByText("8%")).toBeNull();
    expect(screen.getByTestId("chaos-primary")).toHaveTextContent("13%");
  });

  it("explains small-field limits plainly and renders structural zero as 該当馬なし", () => {
    const structuralEvents = AVAILABLE.events.map((event) =>
      event.key === "total_collapse" || event.key === "himo_are"
        ? {
            ...event,
            adjusted_mass: 0,
            raw_mass: 0,
            is_structural_zero: true,
            structural_zero_reason: "field has no double-digit popularity",
          }
        : event,
    );
    render(
      <RaceChaosPanel
        chaos={{
          ...AVAILABLE,
          field_size: 8,
          feasible_support: [6, 21],
          feasible_support_ja: "人気合計は 6〜21 の範囲",
          events: structuralEvents,
          within_field_size_percentile: 20,
        }}
      />,
    );

    expect(screen.getByTestId("chaos-support")).toHaveTextContent(
      "人気合計の可能範囲は 6〜21",
    );
    expect(screen.getByText("二桁人気の馬はいません")).toBeInTheDocument();
    expect(screen.getAllByText(STRUCTURAL_ZERO_LABEL).length).toBeGreaterThan(0);
    expect(screen.queryByText("0%")).toBeNull();
    expect(screen.getByTestId("chaos-relative")).toHaveTextContent(
      "同頭数のレースの中では低め",
    );
  });

  it("shows freshness and calls out a non-confirmatory capture plainly", () => {
    render(
      <RaceChaosPanel
        chaos={{
          ...AVAILABLE,
          snapshot: { ...AVAILABLE.snapshot, capture_strength: "weak" },
        }}
      />,
    );

    expect(screen.getByTestId("chaos-freshness")).toHaveTextContent(
      "発走30分前・13:00 取得",
    );
    expect(screen.getByTestId("chaos-capture-strength")).toHaveTextContent(
      "確認用の捕捉ではありません",
    );
  });

  it("renders on the race page even when the selected run has no horse predictions", async () => {
    server.use(...happyHandlers);
    server.use(
      http.get("*/api/v1/races/:id/predictions", () =>
        HttpResponse.json({
          ...predictionResponse,
          horses: [],
          race_chaos: AVAILABLE,
        }),
      ),
    );
    renderWithProviders(
      <Routes>
        <Route path="/races/:raceId" element={<RaceDetailPage />} />
      </Routes>,
      { route: "/races/200806010111" },
    );

    expect(await screen.findByTestId("race-chaos")).toHaveTextContent("13%");
    expect(screen.getByTestId("no-predictions-cta")).toBeInTheDocument();
  });

  it("keeps percentages integer-only and has no P&L colours, sorting, or CTA", () => {
    const { container } = render(<RaceChaosPanel chaos={AVAILABLE} />);

    expect(container.textContent).not.toMatch(/\d+\.\d+%/);
    expect(container.querySelector(".good, .bad, .danger, .success, .profit")).toBeNull();
    expect(container.querySelector("button, [role='button']")).toBeNull();
  });

  it("renders loading, typed-empty, typed-error, and unavailable as distinct states", () => {
    const { rerender } = render(<RaceChaosPanel chaos={undefined} isLoading />);
    expect(screen.getByTestId("chaos-loading")).toHaveAttribute("data-state", "loading");

    rerender(<RaceChaosPanel chaos={null} />);
    expect(screen.getByTestId("chaos-empty")).toHaveAttribute("data-state", "empty");

    rerender(
      <RaceChaosPanel
        chaos={undefined}
        error={{ status: 503, code: "prediction_unavailable", detail: "fetch failed" }}
      />,
    );
    expect(screen.getByTestId("chaos-error")).toHaveAttribute("data-state", "error");
    expect(screen.getByTestId("chaos-error")).toHaveTextContent("prediction_unavailable");

    rerender(
      <RaceChaosPanel
        chaos={{
          status: "unavailable",
          band_axis: "p_s_ge_20",
          unavailable_reason: "no_snapshot",
        }}
      />,
    );
    expect(screen.getByTestId("chaos-unavailable")).toHaveAttribute(
      "data-state",
      "unavailable",
    );
  });

  it("does not render forbidden confidence or value wording", () => {
    const { container } = render(<RaceChaosPanel chaos={AVAILABLE} />);
    expect(container.textContent).not.toMatch(/暫定|妙味|edge|儲|利益|EV 中立/i);
  });
});
