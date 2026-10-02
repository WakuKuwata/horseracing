import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ATTENTION_SCOPE,
  PROFIT_COLOUR_SELECTOR,
  UNMEASURED_ODDS_DRIFT,
} from "../lib/forbiddenPhrases";
import { ATTENTION_RULES_DISCLAIMER } from "../tests/fixtures";
import { ATTENTION_NOTE_TEXT, AttentionNote } from "./AttentionNote";

// spec FR-011, verbatim (a test-side copy so a silent edit of the constant fails here).
const FR_011 =
  "注目条件は過去データで最も有望だった条件で、検証済みの条件ではありません。S1・S2 は結果を見てから見つけた条件で、過去検証の p 値は多重探索を補正していません。選定には現在のオッズを使うため、購入時点の価格が違えば結果も変わります。注目条件はレースの最初の計算時点で判定し、その後のオッズ変化では付け外ししません。的中や利益を保証するものではありません。";

describe("AttentionNote (138 FR-011)", () => {
  it("renders the FR-011 text verbatim, always visible", () => {
    render(<AttentionNote />);
    const note = screen.getByTestId("attention-note");
    expect(note).toHaveTextContent("注目条件について");
    expect(screen.getByTestId("attention-note-text").textContent).toBe(FR_011);
    expect(ATTENTION_NOTE_TEXT).toBe(FR_011);
    // the judgment-time rule (US1 scenario 9) and the honesty sentences are all present
    expect(note).toHaveTextContent("レースの最初の計算時点で判定し");
    expect(note).toHaveTextContent("検証済みの条件ではありません");
    expect(note).toHaveTextContent("多重探索を補正していません");
    // never folded away in a <details>
    expect(note.closest("details")).toBeNull();
    expect(note.querySelector("details")).toBeNull();
  });

  it("is NOT the API's rules-list disclaimer (that one lacks the price and first-computation sentences)", () => {
    expect(ATTENTION_NOTE_TEXT).not.toBe(ATTENTION_RULES_DISCLAIMER);
    expect(ATTENTION_RULES_DISCLAIMER).not.toMatch(/最初の計算/);
  });

  it("says a failed /attention fetch neutrally (no alert role, no error colour)", () => {
    const { container } = render(
      <AttentionNote error={{ status: 503, code: "attention_unavailable", detail: "down" }} />,
    );
    expect(screen.getByTestId("attention-error").textContent).toBe(
      "注目条件を取得できませんでした(HTTP 503 attention_unavailable)",
    );
    // the FR-011 text stays, the loading line does not appear alongside the error
    expect(screen.getByTestId("attention-note-text").textContent).toBe(FR_011);
    expect(screen.queryByTestId("attention-loading")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelector(".state--error, .refresh__status--error")).toBeNull();
    expect(container.textContent).not.toMatch(ATTENTION_SCOPE);
  });

  it("loading, error and loaded are three distinct states (015)", () => {
    const { rerender } = render(<AttentionNote isLoading />);
    expect(screen.getByTestId("attention-loading")).toHaveTextContent("注目条件を読み込み中…");
    expect(screen.queryByTestId("attention-error")).toBeNull();
    rerender(<AttentionNote />);
    expect(screen.queryByTestId("attention-loading")).toBeNull();
    expect(screen.queryByTestId("attention-error")).toBeNull();
  });

  it("uses no scoped forbidden phrase, no drift claim, no controls and no P/L colour", () => {
    const { container } = render(<AttentionNote />);
    expect(container.textContent).not.toMatch(ATTENTION_SCOPE);
    expect(container.textContent).not.toMatch(UNMEASURED_ODDS_DRIFT);
    expect(container.textContent).not.toMatch(/印/);
    for (const el of Array.from(container.querySelectorAll("[aria-label], [title]"))) {
      expect(el.getAttribute("aria-label") ?? "").not.toMatch(/印|推奨|おすすめ/);
      expect(el.getAttribute("title") ?? "").not.toMatch(ATTENTION_SCOPE);
    }
    expect(container.querySelector("button, [role='button'], a, input, select")).toBeNull();
    expect(container.querySelector(`${PROFIT_COLOUR_SELECTOR}, .up, .down`)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
