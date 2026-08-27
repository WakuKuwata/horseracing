import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PROFIT_LANGUAGE } from "../lib/forbiddenPhrases";

import { RaceDispersionPanel } from "./RaceDispersionPanel";
import { RaceChaosPanel } from "./RaceChaosPanel";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import type { PredictionResponse, RaceDispersion } from "../api/types";

const AVAILABLE: RaceDispersion = {
  available: true,
  unavailable_reason: null,
  band: "somewhat_open",
  normalized_entropy: 0.842,
  favorite_win_prob: 0.31,
  top3_cumulative: 0.68,
  model_delta: null,
  odds_as_of: "2026-07-05T09:30:00Z",
  odds_source: "final",
  is_pseudo: true,
  boundary_version: "dispbands-v1",
};

type RaceChaos = NonNullable<PredictionResponse["race_chaos"]>;
const CHAOS_AVAILABLE: Extract<RaceChaos, { status: "available" }> = {
  status: "available",
  unavailable_reason: null,
  band: "t3_mid",
  band_axis: "p_s_ge_20",
  field_size: 10,
  feasible_support: [6, 27],
  feasible_support_ja: "人気合計は 6〜27 の範囲",
  events: [
    {
      key: "s_ge_20", label_ja: "人気順合計が20以上",
      adjusted_mass: 0.12, raw_mass: 0.08,
      is_structural_zero: false, structural_zero_reason: null, lambda_sensitive: true,
    },
    {
      key: "himo_are", label_ja: "1〜3番人気が勝ち、2着か3着に二桁人気",
      adjusted_mass: 0.23, raw_mass: 0.2,
      is_structural_zero: false, structural_zero_reason: null, lambda_sensitive: true,
    },
    {
      key: "total_collapse", label_ja: "二桁人気が勝つ",
      adjusted_mass: 0.03, raw_mass: 0.03,
      is_structural_zero: false, structural_zero_reason: null, lambda_sensitive: false,
    },
  ],
  expected_top3_popularity_sum: 12.5,
  within_field_size_percentile: 65,
  calibration_status: "provisional",
  calibration_basis: "closing_history_2020_2023",
  is_market_derived: true,
  is_pseudo: true,
  snapshot: {
    captured_at: "2026-07-26T04:00:00Z", source: "netkeiba", seconds_to_post: 1800,
    capture_strength: "confirmatory", content_digest: "c".repeat(64),
    snapshot_id: "00000000-0000-4000-8000-000000000084",
  },
  artifact_version: "chaosbands-v1",
  artifact_digest: "a".repeat(64),
  readout_source: "persisted",
  persisted_artifact_digest: "a".repeat(64),
};

describe("RaceDispersionPanel", () => {
  it("renders a collapsed market-support concentration detail with its scale name", () => {
    render(<RaceDispersionPanel dispersion={AVAILABLE} />);
    const panel = screen.getByTestId("race-dispersion");
    expect(panel.tagName).toBe("DETAILS");
    expect(panel).not.toHaveAttribute("open");
    expect(screen.getByText("市場の支持集中度")).toBeInTheDocument();
    expect(screen.getByText("単勝支持の5段階スケール")).toBeInTheDocument();
    expect(screen.getByTestId("dispersion-band")).toHaveTextContent("やや波乱");
    // raw numbers always present
    expect(screen.getByText("31.0%")).toBeInTheDocument(); // favourite win prob
    expect(screen.getByText("68.0%")).toBeInTheDocument(); // top-3 share
    expect(screen.getByText("0.842")).toBeInTheDocument(); // normalised entropy
  });

  it("stamps a pseudo badge on every 066 and race_chaos market-q figure (015 invariant)", () => {
    const { container } = render(
      <>
        <RaceDispersionPanel dispersion={AVAILABLE} />
        <RaceChaosPanel chaos={CHAOS_AVAILABLE} />
      </>,
    );
    assertPseudoLabelCoverage(
      container,
      ["31.0%", "68.0%", "0.842", "やや波乱", "12%", "23%", "3%", "12.5", "標準"],
    );
  });

  it("removes outcome-claiming captions from the deprecated 066 scale", () => {
    const { container } = render(<RaceDispersionPanel dispersion={AVAILABLE} />);
    expect(container.textContent).not.toMatch(
      /本命中心で決まりやすい|やや本命中心|標準的なばらつき|やや割れやすい|総流れ・本命が飛びやすい/,
    );
  });

  it("uses NO profit/edge/value wording and NO P&L colour, no sorting", () => {
    const { container } = render(<RaceDispersionPanel dispersion={AVAILABLE} />);
    expect(container.textContent).not.toMatch(PROFIT_LANGUAGE);
    expect(container.querySelector(".good, .bad, .danger, .success, .profit")).toBeNull();
    expect(container.querySelector("button, [role='button']")).toBeNull(); // no sort controls
  });

  it("shows an honest unavailable state (no fallback to model p)", () => {
    render(
      <RaceDispersionPanel
        dispersion={{ ...AVAILABLE, available: false, unavailable_reason: "no_market_odds",
          band: null, normalized_entropy: null, favorite_win_prob: null, top3_cumulative: null }}
      />,
    );
    expect(screen.getByTestId("dispersion-unavailable")).toHaveTextContent("市場オッズが無いため");
    expect(screen.queryByTestId("dispersion-band")).toBeNull();
  });

  it("shows raw numbers with band omitted when no boundary artifact loaded (F8)", () => {
    render(<RaceDispersionPanel dispersion={{ ...AVAILABLE, band: null, boundary_version: null }} />);
    expect(screen.getByTestId("dispersion-no-boundary")).toBeInTheDocument();
    expect(screen.getByText("31.0%")).toBeInTheDocument(); // raw numbers still there
  });

  it("renders the neutral model_delta line when a calibrated delta is present", () => {
    render(
      <RaceDispersionPanel
        dispersion={{
          ...AVAILABLE,
          model_delta: {
            normalized_entropy_delta: 0.12,
            direction: "model_more_open",
            calibrator_version: "pcal-v1",
          },
        }}
      />,
    );
    const row = screen.getByTestId("dispersion-model-delta");
    expect(row).toHaveTextContent("市場より荒れ寄り");
    expect(row).toHaveTextContent("0.120");
    // neutral wording only — no buy/edge/value framing on the model line either.
    expect(row.textContent).not.toMatch(/妙味|買|勝てる|お得|edge/);
  });

  it("omits the model_delta line when null (no calibrator loaded)", () => {
    render(<RaceDispersionPanel dispersion={AVAILABLE} />);
    expect(screen.queryByTestId("dispersion-model-delta")).toBeNull();
  });

  it("renders nothing when dispersion is absent", () => {
    const { container } = render(<RaceDispersionPanel dispersion={null} />);
    expect(container.firstChild).toBeNull();
  });
});
