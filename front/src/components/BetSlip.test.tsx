import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HorseEntry, RecommendationRow } from "../api/types";
import { BetSlip } from "./BetSlip";

function makeRow(over: Partial<RecommendationRow> & { recommendation_id: string }): RecommendationRow {
  return {
    bet_type: "win",
    selection: [1],
    stake_fraction: 0.02,
    market_odds_used: 3.2,
    estimated_market_odds_used: null,
    is_estimated_odds: false,
    pseudo_odds: 3.1,
    pseudo_roi: 0.35,
    double_pseudo: false,
    logic_version: "lv",
    computed_at: "2008-05-31T22:30:00Z",
    prediction_run_id: "run-abc",
    settled: false,
    dead_heat: false,
    ...over,
  } as RecommendationRow;
}

const entries: HorseEntry[] = [
  { horse_id: "h1", horse_number: 1, entry_status: "active", horse_name: "アルファ", frame: 1 },
  { horse_id: "h2", horse_number: 2, entry_status: "active", horse_name: null, frame: 5 },
  { horse_id: "h3", horse_number: 3, entry_status: "active", horse_name: "ガンマ", frame: null },
] as HorseEntry[];

describe("BetSlip — summary consistency (SC-007 / contract §2)", () => {
  it("sums only convertible rows, counts match, integer % of budget", () => {
    render(
      <BetSlip
        items={[
          makeRow({ recommendation_id: "a", stake_fraction: 0.024 }), // ¥200
          makeRow({ recommendation_id: "b", stake_fraction: 0.0123 }), // ¥100
          makeRow({ recommendation_id: "c", stake_fraction: null }), // reference
          makeRow({ recommendation_id: "d", stake_fraction: 0.007 }), // too_small (70円)
        ]}
        budget={10000}
        winPolicyStatus="generated"
      />,
    );
    const summary = screen.getByTestId("slip-summary");
    expect(summary).toHaveTextContent("¥300");
    expect(summary).toHaveTextContent("2点・予算の3%");
    // every displayed yen amount is a multiple of 100 and their sum equals the summary
    expect(screen.getByTestId("bet-slip-card-a")).toHaveTextContent("¥200");
    expect(screen.getByTestId("bet-slip-card-b")).toHaveTextContent("¥100");
    expect(screen.getByTestId("bet-slip-card-c")).toHaveTextContent("参考表示");
    expect(screen.getByTestId("bet-slip-card-d")).toHaveTextContent("少額のため見送り");
  });

  it("reports over-budget neutrally without shrinking amounts", () => {
    render(
      <BetSlip
        items={[
          makeRow({ recommendation_id: "a", stake_fraction: 0.8 }), // ¥8,000
          makeRow({ recommendation_id: "b", stake_fraction: 0.4 }), // ¥4,000
        ]}
        budget={10000}
        winPolicyStatus="generated"
      />,
    );
    expect(screen.getByTestId("slip-summary")).toHaveTextContent("¥12,000");
    expect(screen.getByTestId("over-budget-note")).toHaveTextContent("合計が予算を超えています");
    expect(screen.getByTestId("bet-slip-card-a")).toHaveTextContent("¥8,000"); // untouched
  });

  it("shows ¥0 / 0点 plus an explicit note when every row rounds below ¥100", () => {
    render(
      <BetSlip
        items={[
          makeRow({ recommendation_id: "a", stake_fraction: 0.005 }),
          makeRow({ recommendation_id: "b", stake_fraction: 0.003 }),
        ]}
        budget={10000}
        winPolicyStatus="generated"
      />,
    );
    const summary = screen.getByTestId("slip-summary");
    expect(summary).toHaveTextContent("¥0");
    expect(summary).toHaveTextContent("0点");
    expect(screen.getByTestId("all-too-small")).toHaveTextContent("全買い目が少額のため見送り");
  });

  it("renders NO summary when the budget is unset (fallback shows ratios + strength)", () => {
    render(
      <BetSlip
        items={[makeRow({ recommendation_id: "a", stake_fraction: 0.02 })]}
        budget={null}
        winPolicyStatus="generated"
      />,
    );
    expect(screen.queryByTestId("slip-summary")).toBeNull();
    expect(screen.queryByText(/¥/)).toBeNull(); // no yen amounts at all
    const card = screen.getByTestId("bet-slip-card-a");
    expect(card).toHaveTextContent("2.00%"); // Kelly fraction fallback
    expect(within(card).getByText("厚め")).toBeInTheDocument(); // strength is budget-independent
  });
});

describe("BetSlip — grouping and API order (contract §4)", () => {
  it("groups by bet type in first-appearance order and keeps rows in API order", () => {
    render(
      <BetSlip
        items={[
          makeRow({ recommendation_id: "t1", bet_type: "trio", selection: [3, 5, 7], stake_fraction: 0.003, pseudo_roi: 0.5 }),
          makeRow({ recommendation_id: "w1", stake_fraction: 0.01, pseudo_roi: 0.1 }),
          // deliberately NON-ascending by amount/roi/horse number inside the trio group
          makeRow({ recommendation_id: "t2", bet_type: "trio", selection: [1, 2, 4], stake_fraction: 0.03, pseudo_roi: 0.9 }),
        ]}
        budget={10000}
        winPolicyStatus="generated"
      />,
    );
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["三連複", "単勝"]); // first-appearance order, not alphabetical
    const cards = screen.getAllByTestId(/bet-slip-card-/).map((c) => c.getAttribute("data-testid"));
    expect(cards).toEqual(["bet-slip-card-t1", "bet-slip-card-t2", "bet-slip-card-w1"]);
  });

  it("bases strength on the RACE-WIDE max, not the per-group max", () => {
    render(
      <BetSlip
        items={[
          // race-wide max 0.03 lives in the win group
          makeRow({ recommendation_id: "w1", stake_fraction: 0.03 }),
          // trio group's own max 0.005 — per-group computation would wrongly crown it 厚め
          makeRow({ recommendation_id: "t1", bet_type: "trio", selection: [1, 2, 3], stake_fraction: 0.005 }),
          // exact third boundaries against the race max stay in the LOWER tier
          makeRow({ recommendation_id: "w2", selection: [2], stake_fraction: 0.01 }),
          makeRow({ recommendation_id: "w3", selection: [3], stake_fraction: 0.02 }),
        ]}
        budget={100000}
        winPolicyStatus="generated"
      />,
    );
    expect(within(screen.getByTestId("bet-slip-card-w1")).getByText("厚め")).toBeInTheDocument();
    expect(within(screen.getByTestId("bet-slip-card-t1")).getByText("抑え")).toBeInTheDocument();
    expect(within(screen.getByTestId("bet-slip-card-w2")).getByText("抑え")).toBeInTheDocument(); // 1/3 → lower
    expect(within(screen.getByTestId("bet-slip-card-w3")).getByText("標準")).toBeInTheDocument(); // 2/3 → lower
  });
});

describe("BetSlip — horse name / frame chips (FR-011/012, independent degradation)", () => {
  it("degrades name and frame independently per horse in a multi-horse selection", () => {
    render(
      <BetSlip
        items={[
          makeRow({ recommendation_id: "t1", bet_type: "trio", selection: [1, 2, 3], stake_fraction: 0.01 }),
        ]}
        budget={10000}
        entries={entries}
        winPolicyStatus="generated"
      />,
    );
    const card = screen.getByTestId("bet-slip-card-t1");
    // horse 1: name + frame 1 (white chip)
    expect(card.querySelector(".frame-chip--1")).toHaveTextContent("1");
    expect(card).toHaveTextContent("アルファ");
    // horse 2: NO name but frame 5 — keeps the frame color
    expect(card.querySelector(".frame-chip--5")).toHaveTextContent("2");
    // horse 3: name but NO frame — neutral chip, name kept
    expect(card.querySelector(".frame-chip--none")).toHaveTextContent("3");
    expect(card).toHaveTextContent("ガンマ");
  });

  it("renders number-only neutral chips when entries are missing entirely", () => {
    render(
      <BetSlip
        items={[makeRow({ recommendation_id: "w9", selection: [9] })]}
        budget={10000}
        winPolicyStatus="generated"
      />,
    );
    const card = screen.getByTestId("bet-slip-card-w9");
    expect(card.querySelector(".frame-chip--none")).toHaveTextContent("9");
  });
});

describe("BetSlip — skip / empty states (FR-030/031)", () => {
  it.each([
    ["no_run", "このレースの予測がまだありません。"],
    ["not_generated", "単勝の買い目はまだ生成されていません。"],
    ["no_win_selected", "単勝は見送りです(policy が条件を満たす買い目を選定しませんでした)。"],
  ])("items=[] × %s shows the policy card, never a blank", (status, message) => {
    render(<BetSlip items={[]} budget={10000} winPolicyStatus={status} />);
    expect(screen.getByTestId("win-skip-reason")).toHaveTextContent(message);
  });

  it("items=[] × generated keeps the existing neutral empty message (fallback)", () => {
    render(<BetSlip items={[]} budget={10000} winPolicyStatus="generated" />);
    expect(screen.getByText("この条件の推奨はありません")).toBeInTheDocument();
  });

  it("shows the win-skip card AND the exotic cards together when only win was skipped", () => {
    render(
      <BetSlip
        items={[
          makeRow({ recommendation_id: "q1", bet_type: "quinella", selection: [1, 2], stake_fraction: 0.01 }),
        ]}
        budget={10000}
        winPolicyStatus="no_win_selected"
      />,
    );
    expect(screen.getByTestId("win-skip-reason")).toHaveTextContent("単勝は見送り");
    expect(screen.getByTestId("bet-slip-card-q1")).toBeInTheDocument();
  });
});
