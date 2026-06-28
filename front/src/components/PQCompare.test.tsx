import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PredictionResponse } from "../api/types";
import { predictionResponse } from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { renderWithProviders } from "../tests/utils";
import { PQCompare } from "./PQCompare";

describe("PQCompare", () => {
  it("shows p and q, badges every q as 市場推定, leaves p unbadged (SC-001/002)", () => {
    const { container } = renderWithProviders(<PQCompare data={predictionResponse} />);

    // p shown (32.0%/18.0%) and q shown (30.0%/20.0%)
    expect(screen.getByText("32.0%")).toBeInTheDocument();
    expect(screen.getByText("18.0%")).toBeInTheDocument();

    // every market q value renders inside a [data-pseudo] node with a badge (single render path, V)
    assertPseudoLabelCoverage(container, ["30.0%", "20.0%"]);
    // model p must NOT be marked pseudo
    expect(screen.getByText("32.0%").closest('[data-pseudo="true"]')).toBeNull();
  });

  it("presents p−q neutrally — no profit language, no edge-based sorting (SC-007)", () => {
    const { container } = renderWithProviders(<PQCompare data={predictionResponse} />);
    // the 020 disclosure that the market out-predicts the model is shown (FR-017)
    expect(screen.getByTestId("market-superiority-note")).toBeInTheDocument();
    // no buy/profit wording in the data table (the disclaimer may say "買い目の推奨ではない")
    const table = container.querySelector("table");
    expect(table?.textContent).not.toMatch(/買い|お買い得|おすすめ|妙味/);
    // rows ordered by model p desc (NOT by p−q edge): h1 (0.32) before h2 (0.18)
    const ids = Array.from(container.querySelectorAll("tbody tr td:nth-child(2)")).map(
      (n) => n.textContent,
    );
    expect(ids).toEqual(["h1", "h2"]);
    // a diff column exists but carries no win/loss colour class
    expect(container.querySelector(".profit, .good, .bad, .up, .down")).toBeNull();
  });

  it("renders q as 未提供 (not 0, not pseudo) when odds are missing (FR-004)", () => {
    const data: PredictionResponse = {
      ...predictionResponse,
      horses: [
        { horse_id: "h1", horse_number: 1, win: 0.32, market_win_prob: 0.3 },
        { horse_id: "h2", horse_number: 2, win: 0.18, market_win_prob: null },
      ],
    };
    const { container } = renderWithProviders(<PQCompare data={data} />);
    const rows = container.querySelectorAll("tbody tr");
    const h2 = rows[1];
    expect(h2.textContent).toContain("—"); // placeholder, never 0%
    // the placeholder is not wrapped as a pseudo value
    expect(h2.querySelector('[data-pseudo="true"]')).toBeNull();
  });

  it("suppresses the p−q divergence when populations differ (canonical_consistent=false, R1)", () => {
    const data: PredictionResponse = { ...predictionResponse, canonical_consistent: false };
    renderWithProviders(<PQCompare data={data} />);
    expect(screen.getByTestId("pq-incomparable")).toBeInTheDocument();
    expect(screen.queryByText(/差 \(p−q\)/)).toBeNull(); // 差 column header absent
  });
});
