import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { MarketEvAvailable, MarketEvUnavailable } from "../api/types";
import {
  EXPECTED_RETURN_SCOPE,
  PROFIT_COLOUR_SELECTOR,
  UNMEASURED_CLAIMS,
} from "../lib/forbiddenPhrases";
import { ExpectedReturnNote } from "./ExpectedReturnNote";

const AVAILABLE: MarketEvAvailable = {
  status: "available",
  race_id: "202609270511",
  model_version: "mev-binary-v2",
  logic_version: "mev-v1",
  // JST 09:10 / 09:15 — the note reads in the race-time zone, like OddsFreshness
  odds_observed_at: "2026-09-27T00:10:00Z",
  computed_at: "2026-09-27T00:15:00Z",
  odds_changed_after_compute: false,
  result_pending_at_compute: true,
  threshold: 1.2,
  is_pseudo: true,
  horses: [
    { horse_id: "h1", horse_number: 1, expected_return: 1.3, odds_used: 5.0,
      exceeds_threshold: true },
  ],
};

const unavailable = (reason: MarketEvUnavailable["reason"]): MarketEvUnavailable => ({
  status: "unavailable", race_id: "202609270511", reason, threshold: 1.2,
});

const VALIDATION =
  "過去検証(2010〜2026 年、各年を前年までのデータで学習): 期待回収率 120% 超の馬の単勝回収率は 100.8%(95% 区間 94〜108%)、2019 年以降は 99.5%。参考として全馬は 72%。120% を超えても利益は確認できていません。";

describe("ExpectedReturnNote (137)", () => {
  it("available: says it is a separate model, when the odds/compute were taken, and the record", () => {
    render(<ExpectedReturnNote marketEv={AVAILABLE} isLoading={false} error={null} />);

    const note = screen.getByTestId("expected-return-note");
    expect(note).toHaveTextContent("期待回収率について");
    expect(screen.getByTestId("expected-return-definition").textContent).toBe(
      "期待回収率は、表の『モデル勝率』とは別の市場連動モデル(mev-binary-v2)が、単勝オッズと各馬の過去成績から推定した勝率 × 単勝オッズです。",
    );
    expect(screen.getByTestId("expected-return-times").textContent).toBe(
      "オッズ取得 2026/09/27 09:10 ・計算 2026/09/27 09:15",
    );
    expect(screen.getByTestId("expected-return-validation").textContent).toBe(VALIDATION);
    expect(screen.getByTestId("expected-return-uncertainty").textContent).toBe(
      "2025〜26 年は保存オッズに発走前の値が混ざるため検証の精度が落ちます。発走前オッズでの前向きの検証はまだ行っておらず、120%超の目印はその前に付けているものです。",
    );
    expect(screen.getByTestId("expected-return-disclaimer").textContent).toBe(
      "的中や利益を保証するものではありません。",
    );
    // pending at compute + unchanged odds → neither conditional sentence
    expect(screen.queryByTestId("expected-return-odds-changed")).toBeNull();
    expect(screen.queryByTestId("expected-return-post-result")).toBeNull();
    // always visible: never folded away in a <details>
    expect(note.closest("details")).toBeNull();
    expect(note.querySelector("details")).toBeNull();
  });

  it("says the odds changed after the compute (display stays on the compute-time odds)", () => {
    render(
      <ExpectedReturnNote
        marketEv={{ ...AVAILABLE, odds_changed_after_compute: true }}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getByTestId("expected-return-odds-changed").textContent).toBe(
      "計算後に単勝オッズが変わっています(表示は計算時のオッズ基準)",
    );
  });

  it("marks a value computed after the result as an after-the-fact reference", () => {
    render(
      <ExpectedReturnNote
        marketEv={{ ...AVAILABLE, result_pending_at_compute: false }}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getByTestId("expected-return-post-result").textContent).toBe(
      "結果確定後のオッズで計算した事後の参考値です",
    );
  });

  it.each([
    ["not_computed", "このレースの期待回収率はまだ計算されていません"],
    ["field_changed", "出走馬が変わったため再計算待ちです"],
    ["odds_unavailable", "単勝オッズがそろっていないため表示していません"],
  ] as const)("unavailable(%s) shows its typed reason only", (reason, text) => {
    render(
      <ExpectedReturnNote marketEv={unavailable(reason)} isLoading={false} error={null} />,
    );
    expect(screen.getByTestId("expected-return-unavailable").textContent).toBe(text);
    expect(screen.queryByTestId("expected-return-definition")).toBeNull();
    expect(screen.queryByTestId("expected-return-times")).toBeNull();
  });

  it("shows loading and errors neutrally (no alert role, no error colour)", () => {
    const { container, rerender } = render(
      <ExpectedReturnNote marketEv={undefined} isLoading={true} error={null} />,
    );
    expect(screen.getByTestId("expected-return-loading")).toHaveTextContent("読み込み中");

    rerender(
      <ExpectedReturnNote
        marketEv={undefined}
        isLoading={false}
        error={{ status: 503, code: "error", detail: "boom" }}
      />,
    );
    expect(screen.getByTestId("expected-return-error")).toHaveTextContent(
      "期待回収率を取得できませんでした",
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelector(".state--error")).toBeNull();
  });

  it.each([
    ["available", AVAILABLE],
    ["available (changed, post-result)", {
      ...AVAILABLE, odds_changed_after_compute: true, result_pending_at_compute: false,
    }],
    ["not_computed", unavailable("not_computed")],
    ["field_changed", unavailable("field_changed")],
    ["odds_unavailable", unavailable("odds_unavailable")],
  ])("%s: no scoped forbidden phrase, no buy wording, no controls, no P/L colour", (_l, ev) => {
    const { container } = render(
      <ExpectedReturnNote marketEv={ev} isLoading={false} error={null} />,
    );
    expect(container.textContent).not.toMatch(EXPECTED_RETURN_SCOPE);
    expect(container.textContent).not.toMatch(UNMEASURED_CLAIMS);
    expect(container.textContent).not.toMatch(/買/);
    expect(container.querySelector("button, [role='button'], a, input, select")).toBeNull();
    expect(container.querySelector(`${PROFIT_COLOUR_SELECTOR}, .up, .down`)).toBeNull();
  });
});
