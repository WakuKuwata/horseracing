import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Explanation } from "../api/types";
import { ExplanationPanel } from "./ExplanationPanel";

const EXP: Explanation = {
  method: "lgbm_pred_contrib", method_version: 1, k: 2,
  base_value: -3.0, score: -2.4, other_contribution: 0.1,
  items: [
    { feature: "te_jockey_id", value: 0.08, contribution: 0.5 },
    { feature: "unknown_new_feature", value: "x", contribution: -0.2 },
  ],
};

const NOTE_SCORE = /校正・レース内正規化前のスコア/;
const NOTE_CAUSAL = /因果関係を示すものではありません/;

describe("ExplanationPanel", () => {
  it("renders score contributions with Japanese labels and values", () => {
    render(<ExplanationPanel explanation={EXP} />);
    expect(screen.getByText("騎手成績（統計）")).toBeInTheDocument();
    expect(screen.getByText("+0.500")).toBeInTheDocument();
    expect(screen.getByText("その他の特徴（合算）")).toBeInTheDocument();
  });

  it("tags model-internal (TE) features with a 導出特徴 badge", () => {
    render(<ExplanationPanel explanation={EXP} />);
    expect(screen.getByText("導出特徴")).toBeInTheDocument();
  });

  it("fails open on unknown feature names (shows raw name, not hidden)", () => {
    render(<ExplanationPanel explanation={EXP} />);
    expect(screen.getByText("unknown_new_feature")).toBeInTheDocument();
  });

  it("INVARIANT: never renders contributions without BOTH limitation notes", () => {
    render(<ExplanationPanel explanation={EXP} />);
    expect(screen.getByText(NOTE_SCORE)).toBeInTheDocument();
    expect(screen.getByText(NOTE_CAUSAL)).toBeInTheDocument();
  });

  it("shows 未提供 (not error/blank) when explanation is null", () => {
    render(<ExplanationPanel explanation={null} />);
    expect(screen.getByText(/未提供/)).toBeInTheDocument();
  });
});
