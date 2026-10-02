import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AttentionDayItem, AttentionDayResponse } from "../api/types";
import { assertListDiscipline } from "../tests/attentionScope";
import { attentionDayFixture, attentionDayItemFixture, stageDetail } from "../tests/fixtures";
import { renderWithProviders } from "../tests/utils";
import { AttentionDayList } from "./AttentionDayList";

const DATE = "2026-10-03";
// Render time fixed: 2026-10-03 14:00 JST.
const NOW = new Date("2026-10-03T05:00:00Z");

function item(overrides: Partial<AttentionDayItem>): AttentionDayItem {
  return attentionDayItemFixture({
    has_results: false,
    venue_code: "05",
    ...overrides,
  });
}

/** Three chip horses in the API's post order (the last one has no post time). */
function day(): AttentionDayResponse {
  return attentionDayFixture(DATE, [
    item({
      race_id: "202610030501", race_number: 1, post_time: "2026-10-03T05:30:00Z",
      horse_id: "a1", horse_number: 7, horse_name: "アサノホシ",
      chip_rule: "S1", chip_stage: stageDetail("observing"), chip_s2: true, chip_now: "matches",
      levels: { backtest: 3, prospective: 2, price_noise: 2 },
      current_odds_observed_at: "2026-10-03T04:55:00Z", // 5 min ago → 価格鮮度 10 分以内
    }),
    item({
      race_id: "202610030511", race_number: 11, post_time: "2026-10-03T06:40:00Z",
      horse_id: "b1", horse_number: 3, horse_name: "ビーワン",
      chip_rule: "S4", chip_stage: stageDetail("failed", 300), chip_now: "matches",
      levels: { backtest: 2, prospective: 1, price_noise: 2 },
      current_odds_observed_at: "2026-10-03T04:30:00Z", // 30 min ago → 60 分以内
    }),
    item({
      race_id: "202610030612", race_number: 12, post_time: null, venue_code: "06",
      horse_id: "c1", horse_number: 12, horse_name: null,
      chip_rule: "S3", chip_stage: stageDetail("observing", null, true), chip_now: "no_longer",
      levels: { backtest: 2, prospective: 2, price_noise: 2 },
      current_odds_observed_at: null,
    }),
  ]);
}

function render(d: AttentionDayResponse | undefined = day(), extra: { isLoading?: boolean } = {}) {
  return renderWithProviders(
    <AttentionDayList day={d} isLoading={extra.isLoading ?? false} error={null} now={NOW} />,
  );
}

function rows(): HTMLElement[] {
  return Array.from(
    screen.getByTestId("attention-day-list").querySelectorAll<HTMLElement>("tbody tr"),
  );
}

describe("AttentionDayList (138 T037)", () => {
  it("lists rows in the API (post) order; unknown post time is labelled and stays last", () => {
    render();
    const ids = rows().map((r) => r.getAttribute("data-testid"));
    expect(ids).toEqual([
      "attention-day-row-202610030501-a1",
      "attention-day-row-202610030511-b1",
      "attention-day-row-202610030612-c1",
    ]);
    const posts = rows().map((r) => within(r).getByTestId("attention-day-post").textContent);
    expect(posts).toEqual(["14:30", "15:40", "発走時刻不明"]);
  });

  it("never re-sorts by emphasis: a weaker row given first stays first", () => {
    const d = day();
    // make the 2nd row the strongest one possible; the order must not change
    d.items[1] = { ...d.items[1], chip_stage: stageDetail("observing"),
      levels: { backtest: 3, prospective: 3, price_noise: 3 },
      current_odds_observed_at: "2026-10-03T04:58:00Z" };
    render(d);
    expect(rows().map((r) => r.getAttribute("data-level"))).toEqual(["2", "3", "1"]);
    expect(rows()[0]).toHaveAttribute("data-testid", "attention-day-row-202610030501-a1");
  });

  it("shows race (link), 馬番, 馬名, the chip (ID・stage), S2 sub chip and the progress dots", () => {
    render();
    const [a, b, c] = rows();
    const link = within(a).getByRole("link", { name: "東京 1R" });
    expect(link).toHaveAttribute("href", "/races/202610030501");
    expect(a).toHaveTextContent("7");
    expect(a).toHaveTextContent("アサノホシ");

    const chip = within(a).getByRole("note", { name: "注目条件 S1・観察中" });
    expect(chip).toHaveTextContent("注目条件 S1・観察中");
    // min(3, 2, 2, freshness 3) = 2 → outline
    expect(chip).toHaveClass("attn-chip", "attn-chip--2");
    expect(within(a).getByText("S2")).toHaveClass("attn-chip--sub");
    expect(within(a).getByTestId("attention-day-progress").textContent).toBe("検証の進み具合●●○(3 段階中 2)");
    expect(within(a).getByTestId("attention-day-odds-time").textContent).toBe(
      "13:55(価格鮮度 10 分以内)",
    );
    expect(within(a).getByTestId("attention-day-now").textContent).toBe("—");

    // failed: the main label is the stage name, weakest emphasis, no S2 sub chip
    const failed = within(b).getByRole("note", { name: "注目条件 S4・300 点不通過" });
    expect(failed).toHaveClass("attn-chip--1");
    expect(within(b).queryByText("S2")).toBeNull();
    expect(within(b).getByTestId("attention-day-progress").textContent).toBe("検証の進み具合●○○(3 段階中 1)");
    expect(within(b).getByTestId("attention-day-odds-time").textContent).toBe(
      "13:30(価格鮮度 60 分以内)",
    );

    // judged-only + no current value + pending judgment
    expect(within(c).getByRole("link", { name: "中山 12R" })).toBeInTheDocument();
    expect(within(c).getByRole("note", { name: "注目条件 S3・観察中・判定待ち" })).toHaveClass(
      "attn-chip--1",
    );
    expect(within(c).getByTestId("attention-day-now").textContent).toBe("判断時点のみ該当");
    expect(within(c).getByTestId("attention-day-odds-time").textContent).toBe("最新の計算なし");
    expect(c).toHaveTextContent("—"); // horse name unknown → placeholder, never blank
  });

  it("level 3 only with all four axes at 3 (synthetic: the frozen values never reach it)", () => {
    render(attentionDayFixture(DATE, [
      item({
        race_id: "202610030501", post_time: "2026-10-03T05:30:00Z", chip_now: "matches",
        levels: { backtest: 3, prospective: 3, price_noise: 3 },
        current_odds_observed_at: "2026-10-03T04:52:00Z",
      }),
    ]));
    const [r] = rows();
    expect(within(r).getByRole("note")).toHaveClass("attn-chip--3");
    expect(within(r).getByTestId("attention-day-progress").textContent).toBe("検証の進み具合●●●(3 段階中 3)");
  });

  it("「現在値なし」 forces the weakest emphasis", () => {
    render(attentionDayFixture(DATE, [
      item({
        race_id: "202610030501", post_time: "2026-10-03T05:30:00Z", chip_now: "unknown",
        levels: { backtest: 3, prospective: 3, price_noise: 3 },
        current_odds_observed_at: "2026-10-03T04:58:00Z",
      }),
    ]));
    const [r] = rows();
    expect(within(r).getByTestId("attention-day-now").textContent).toBe("現在値なし");
    expect(within(r).getByRole("note")).toHaveClass("attn-chip--1");
  });

  it("after post the freshness reads 発走後 and the emphasis is the weakest", () => {
    render(attentionDayFixture(DATE, [
      item({
        race_id: "202610030501", post_time: "2026-10-03T04:30:00Z", has_results: true,
        levels: { backtest: 3, prospective: 3, price_noise: 3 },
        current_odds_observed_at: "2026-10-03T04:25:00Z",
      }),
    ]));
    const [r] = rows();
    expect(within(r).getByTestId("attention-day-odds-time").textContent).toBe(
      "13:25(価格鮮度 発走後)",
    );
    expect(within(r).getByRole("note")).toHaveClass("attn-chip--1");
  });

  it("an empty day reads 「該当なし」 (not an error)", () => {
    render(attentionDayFixture(DATE, []));
    expect(screen.getByTestId("attention-day-empty").textContent).toBe("該当なし");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("loading and error are neutral (no alert role)", () => {
    const { rerender } = render(undefined, { isLoading: true });
    expect(screen.getByTestId("attention-day-loading")).toBeInTheDocument();
    rerender(
      <AttentionDayList
        day={undefined}
        isLoading={false}
        error={{ status: 503, code: "error", detail: "boom" }}
        now={NOW}
      />,
    );
    expect(screen.getByTestId("attention-day-error")).toHaveTextContent("HTTP 503");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("display discipline within the list: no steering/印/推奨/おすすめ/通常, no profit colours", () => {
    render();
    const list = screen.getByTestId("attention-day-list");
    assertListDiscipline(list);
    // no threshold numbers, ROI or EV figures in the day list
    expect(list.textContent).not.toMatch(/\d\s*%|倍/);
  });
});
