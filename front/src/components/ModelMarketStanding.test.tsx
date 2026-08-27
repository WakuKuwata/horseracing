import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  PROFIT_COLOUR_SELECTOR,
  PROFIT_LANGUAGE,
  UNMEASURED_CLAIMS,
} from "../lib/forbiddenPhrases";
import { ModelMarketStanding } from "./ModelMarketStanding";

describe("ModelMarketStanding", () => {
  it("勝率モデルと荒れ度分類の評価を分けて常時表示する", () => {
    const { container } = render(<ModelMarketStanding canonicalConsistent={true} />);

    const standing = screen.getByTestId("model-market-standing");
    const winModel = screen.getByTestId("win-model-standing");
    const chaosClassification = screen.getByTestId("chaos-classification-standing");

    expect(winModel).toHaveTextContent(
      "勝率予測では市場評価がモデルを上回っています(検証データ 181,341 件・すべての検証セグメント)。",
    );
    expect(winModel).not.toHaveTextContent(/荒れ度|識別力/);
    expect(chaosClassification).toHaveTextContent(
      "一方、荒れ度の分類には実測上の識別力があります。",
    );
    expect(chaosClassification).not.toHaveTextContent(/市場評価|モデルを上回/);
    expect(standing).toHaveTextContent(
      "モデル勝率との差を利益機会とは解釈せず、レース傾向と見送り判断の補助情報としてご覧ください。",
    );

    // 折りたたみの中では読まれないため、注記自身を常時見える要素として固定する。
    expect(container.querySelector("details")).toBeNull();
    expect(standing.closest("details")).toBeNull();
  });

  it("利益誘導や未実測の主張を含めない", () => {
    const { container } = render(<ModelMarketStanding canonicalConsistent={true} />);

    expect(container.textContent).not.toMatch(PROFIT_LANGUAGE);
    expect(container.textContent).not.toMatch(UNMEASURED_CLAIMS);
  });

  it.each([false, null, undefined])(
    "canonicalConsistent=%s のとき何も描画しない",
    (canonicalConsistent) => {
      const { container } = render(
        <ModelMarketStanding canonicalConsistent={canonicalConsistent} />,
      );

      expect(container.firstChild).toBeNull();
      expect(screen.queryByTestId("model-market-standing")).not.toBeInTheDocument();
    },
  );

  it("canonicalConsistent=true のときだけ描画する", () => {
    render(<ModelMarketStanding canonicalConsistent={true} />);

    expect(screen.getByTestId("model-market-standing")).toBeInTheDocument();
  });

  it("内部の feature 番号を画面に出さない", () => {
    const { container } = render(<ModelMarketStanding canonicalConsistent={true} />);

    expect(container.textContent).not.toMatch(/020|047|feature/i);
  });

  it("損益色や操作要素を持たない", () => {
    const { container } = render(<ModelMarketStanding canonicalConsistent={true} />);

    expect(container.querySelector(PROFIT_COLOUR_SELECTOR)).toBeNull();
    expect(container.querySelector("button, [role='button'], a")).toBeNull();
  });
});
