import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Explanation } from "../api/types";
import { ExplanationPanel } from "./ExplanationPanel";

const V1_EXP: Explanation = {
  method: "lgbm_pred_contrib", method_version: 1, k: 2,
  base_value: -3.0, score: -2.4, other_contribution: 0.1,
  items: [
    { feature: "te_jockey_id", value: 0.08, contribution: 0.5 },
    { feature: "unknown_new_feature", value: "x", contribution: -0.2 },
  ],
};

const V2_EXP: Explanation = {
  method: "lgbm_pred_contrib", method_version: 2, k: 2,
  base_value: -3.0, score: -2.4, other_contribution: 9.999,
  score_centered: -0.1,
  other_contribution_centered: 0.025,
  centering_population_size: 12,
  items: [
    {
      feature: "te_jockey_id",
      value: 0.08,
      contribution: 7.777,
      contribution_centered: 0.125,
    },
    {
      feature: "unknown_new_feature",
      value: "x",
      contribution: -8.888,
      contribution_centered: -0.25,
    },
  ],
};

const NOTE_SCORE_V1 = "校正・レース内正規化前のスコアへの寄与です（最終確率の内訳ではありません）";
const NOTE_SCORE_V2 = "同一レース内の平均に対する、レース内正規化前の相対スコア寄与です（最終確率の内訳ではありません）";
const NOTE_CAUSAL = "相関に基づく説明であり、因果関係を示すものではありません";

describe("ExplanationPanel", () => {
  it("keeps the v1 title, values, other row, and limitation notes unchanged", () => {
    render(<ExplanationPanel explanation={V1_EXP} />);

    expect(screen.getByText("モデルのスコア寄与（上位2要因）")).toBeInTheDocument();
    expect(screen.getByText("騎手成績（統計）")).toBeInTheDocument();
    expect(screen.getByText("+0.500")).toBeInTheDocument();
    expect(screen.getByText("+0.100")).toBeInTheDocument();
    expect(screen.getByText("その他の特徴（合算）")).toBeInTheDocument();
    expect(screen.getByText(`※ ${NOTE_SCORE_V1}`)).toBeInTheDocument();
    expect(screen.getByText(`※ ${NOTE_CAUSAL}`)).toBeInTheDocument();
  });

  it("renders centered v2 contributions in numbers, bars, other row, and note", () => {
    const { container } = render(<ExplanationPanel explanation={V2_EXP} />);

    expect(screen.getByText("レース内でのスコア寄与（上位2要因）")).toBeInTheDocument();
    expect(screen.getByText("+0.125")).toBeInTheDocument();
    expect(screen.getByText("-0.250")).toBeInTheDocument();
    expect(screen.getByText("+0.025")).toBeInTheDocument();
    expect(screen.queryByText("+7.777")).not.toBeInTheDocument();
    expect(screen.queryByText("-8.888")).not.toBeInTheDocument();
    expect(screen.queryByText("+9.999")).not.toBeInTheDocument();
    expect(screen.getByText(`※ ${NOTE_SCORE_V2}`)).toBeInTheDocument();
    expect(screen.getByText(`※ ${NOTE_CAUSAL}`)).toBeInTheDocument();

    const bars = container.querySelectorAll<HTMLElement>(".contrib-fill");
    expect(bars[0]).toHaveStyle({ width: "50%" });
    expect(bars[1]).toHaveStyle({ width: "100%" });
  });

  it("tags model-internal (TE) features with a 導出特徴 badge", () => {
    render(<ExplanationPanel explanation={V1_EXP} />);
    expect(screen.getByText("導出特徴")).toBeInTheDocument();
  });

  it("fails open on unknown feature names (shows raw name, not hidden)", () => {
    render(<ExplanationPanel explanation={V1_EXP} />);
    expect(screen.getByText("unknown_new_feature")).toBeInTheDocument();
  });

  it("shows the existing unavailable message when explanation is null", () => {
    render(<ExplanationPanel explanation={null} />);
    expect(screen.getByText("スコア寄与は未提供です")).toBeInTheDocument();
  });

  it("treats an unknown method version as unavailable", () => {
    render(<ExplanationPanel explanation={{ ...V1_EXP, method_version: 3 }} />);
    expect(screen.getByText("スコア寄与は未提供です")).toBeInTheDocument();
    expect(screen.queryByText("+0.500")).not.toBeInTheDocument();
  });

  it("does not fall back to raw values when a v2 centered contribution is missing", () => {
    const malformed: Explanation = {
      ...V2_EXP,
      items: [{ feature: "te_jockey_id", value: 0.08, contribution: 7.777 }],
    };

    render(<ExplanationPanel explanation={malformed} />);
    expect(screen.getByText("スコア寄与は未提供です（形式不整合）")).toBeInTheDocument();
    expect(screen.queryByText("+7.777")).not.toBeInTheDocument();
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY])(
    "rejects a non-finite v2 centered contribution: %s",
    (contributionCentered) => {
      const malformed: Explanation = {
        ...V2_EXP,
        items: [{
          feature: "te_jockey_id",
          value: 0.08,
          contribution: 7.777,
          contribution_centered: contributionCentered,
        }],
      };

      render(<ExplanationPanel explanation={malformed} />);
      expect(screen.getByText("スコア寄与は未提供です（形式不整合）")).toBeInTheDocument();
      expect(screen.queryByText("+7.777")).not.toBeInTheDocument();
    },
  );

  it.each(["score_centered", "other_contribution_centered"] as const)(
    "treats a null v2 %s as a format mismatch",
    (field) => {
      render(<ExplanationPanel explanation={{ ...V2_EXP, [field]: null }} />);
      expect(screen.getByText("スコア寄与は未提供です（形式不整合）")).toBeInTheDocument();
    },
  );

  it("shows the no-comparable-difference message when v2 items are empty", () => {
    render(<ExplanationPanel explanation={{
      ...V2_EXP,
      score_centered: 0,
      other_contribution_centered: 0,
      items: [],
    }} />);

    expect(screen.getByText("このレースでは比較できる差がありません")).toBeInTheDocument();
    expect(screen.getByText(`※ ${NOTE_SCORE_V2}`)).toBeInTheDocument();
    // No zero-padding: with nothing to compare there is no 上位N heading and no "その他" row —
    // both would read as "factors were found" (contract §4 bans padding an empty panel).
    expect(screen.queryByText(/上位/)).not.toBeInTheDocument();
    expect(screen.queryByText("その他の特徴（合算）")).not.toBeInTheDocument();
  });
});
