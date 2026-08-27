import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  PROFIT_COLOUR_SELECTOR,
  PROFIT_LANGUAGE,
  UNMEASURED_ODDS_DRIFT,
} from "../lib/forbiddenPhrases";
import { OddsFreshness } from "./OddsFreshness";

const ODDS_AS_OF = "2026-07-05T09:30:00Z";
const POST_TIME = "2026-07-05T16:10:00Z";

describe("OddsFreshness", () => {
  it("絶対時刻と発走までの相対時刻を併記する", () => {
    const { container } = render(
      <OddsFreshness oddsAsOf={ODDS_AS_OF} postTime={POST_TIME} hasResults={false} />,
    );

    expect(screen.getByTestId("odds-freshness")).toHaveTextContent(
      "2026/07/05 18:30 時点のオッズ (発走約 7 時間前)",
    );
    // 折りたたみ内では判断時に見落とされるため、常時見える注記であることも固定する。
    expect(container.querySelector("details")).toBeNull();
  });

  it("発走時刻が無いとき相対時刻を推測しない", () => {
    render(<OddsFreshness oddsAsOf={ODDS_AS_OF} postTime={null} hasResults={false} />);

    const note = screen.getByTestId("odds-freshness");
    expect(note).toHaveTextContent("2026/07/05 18:30 時点のオッズ");
    expect(note).toHaveTextContent("発走時刻未登録のため残り時間は表示できません");
    expect(note).not.toHaveTextContent(/発走約 \d+ 時間前/);
  });

  it.each([null, undefined])("oddsAsOf=%s のとき取得時点を不明と明示する", (oddsAsOf) => {
    render(<OddsFreshness oddsAsOf={oddsAsOf} postTime={POST_TIME} hasResults={false} />);

    expect(screen.getByTestId("odds-freshness")).toHaveTextContent("取得時点: 不明");
  });

  it("結果確定済みなら将来形の注意を出さない", () => {
    render(<OddsFreshness oddsAsOf={ODDS_AS_OF} postTime={POST_TIME} hasResults={true} />);

    expect(screen.getByTestId("odds-freshness")).not.toHaveTextContent(
      "動く可能性があります",
    );
  });

  it.each([false, null, undefined])(
    "hasResults=%s のとき発走まで動く可能性を表示する",
    (hasResults) => {
      render(
        <OddsFreshness
          oddsAsOf={ODDS_AS_OF}
          postTime={POST_TIME}
          hasResults={hasResults}
        />,
      );

      expect(screen.getByTestId("odds-freshness")).toHaveTextContent(
        "最終オッズではなく、発走までに動く可能性があります",
      );
    },
  );

  it("市場評価・EV・疑似 ROI が同じ時点のオッズに依存すると明示する", () => {
    render(<OddsFreshness oddsAsOf={ODDS_AS_OF} postTime={POST_TIME} hasResults={false} />);

    expect(screen.getByTestId("odds-freshness")).toHaveTextContent(
      "買い目を作った時点で凍結したオッズ",
    );
  });

  it("利益誘導や未測定のオッズ変動量を含めない", () => {
    const { container } = render(
      <OddsFreshness oddsAsOf={ODDS_AS_OF} postTime={POST_TIME} hasResults={false} />,
    );

    expect(container.textContent).not.toMatch(PROFIT_LANGUAGE);
    expect(container.textContent).not.toMatch(UNMEASURED_ODDS_DRIFT);
  });

  it("損益色や操作要素を持たない", () => {
    const { container } = render(
      <OddsFreshness oddsAsOf={ODDS_AS_OF} postTime={POST_TIME} hasResults={false} />,
    );

    expect(container.querySelector(PROFIT_COLOUR_SELECTOR)).toBeNull();
    expect(container.querySelector("button, [role='button']")).toBeNull();
  });
});

it("依存の説明は、オッズの取得時刻が無いときは出さない", () => {
  // 存在しない時刻に依存するものは無い。出すと「市場評価がある」かのように読める
  // (予測もオッズも無いレースで実画面を見て気づいた)。
  const { queryByTestId, container } = render(
    <OddsFreshness oddsAsOf={null} postTime={null} hasResults={null} />,
  );
  expect(queryByTestId("odds-dependency")).toBeNull();
  expect(container.textContent).toContain("取得時点: 不明");
});
