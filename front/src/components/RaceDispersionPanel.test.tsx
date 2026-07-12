import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RaceDispersionPanel } from "./RaceDispersionPanel";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import type { RaceDispersion } from "../api/types";

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

describe("RaceDispersionPanel", () => {
  it("renders the band with raw numbers beside it (no false precision from a lone label)", () => {
    render(<RaceDispersionPanel dispersion={AVAILABLE} />);
    expect(screen.getByTestId("dispersion-band")).toHaveTextContent("やや波乱");
    // raw numbers always present
    expect(screen.getByText("31.0%")).toBeInTheDocument(); // favourite win prob
    expect(screen.getByText("68.0%")).toBeInTheDocument(); // top-3 share
    expect(screen.getByText("0.842")).toBeInTheDocument(); // normalised entropy
  });

  it("stamps a pseudo badge on every market-q figure (015 invariant)", () => {
    const { container } = render(<RaceDispersionPanel dispersion={AVAILABLE} />);
    assertPseudoLabelCoverage(container, ["31.0%", "68.0%", "0.842", "やや波乱"]);
  });

  it("uses NO profit/edge/value wording and NO P&L colour, no sorting", () => {
    const { container } = render(<RaceDispersionPanel dispersion={AVAILABLE} />);
    expect(container.textContent).not.toMatch(/妙味|危険|儲|回収率|edge|買うべき|勝てる|お得/);
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
