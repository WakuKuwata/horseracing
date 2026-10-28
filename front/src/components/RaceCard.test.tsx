import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { MarketEvDayItem, RaceSummary } from "../api/types";
import { marketEvDayItemFixture } from "../tests/fixtures";
import { RaceCard, marketEvHint } from "./RaceCard";

const base: RaceSummary = {
  race_id: "202505040301",
  race_date: "2025-10-11",
  venue_code: "05",
  race_number: 1,
  race_name: "２歳未勝利",
  race_class: "未勝利",
  track_type: "芝",
  distance: 1600,
  has_results: true,
};

function renderCard(race: RaceSummary, marketEv?: MarketEvDayItem | null) {
  return render(
    <MemoryRouter>
      <RaceCard race={race} marketEv={marketEv} />
    </MemoryRouter>,
  );
}

describe("RaceCard post time", () => {
  it("shows 発走 time in JST when post_time is present", () => {
    renderCard({ ...base, post_time: "2025-10-11T10:05:00+09:00" });
    expect(screen.getByLabelText("発走時刻")).toHaveTextContent("発走 10:05");
  });

  it("omits the post-time chip for JRA-VAN-only races (no post_time)", () => {
    renderCard({ ...base, post_time: null });
    expect(screen.queryByLabelText("発走時刻")).toBeNull();
  });
});

// B7: the day listing's 期待回収率 hint — counts and state only, never a per-horse value.
describe("RaceCard 期待回収率 hint (B7)", () => {
  it("draws nothing while the day listing is unknown (loading / failed)", () => {
    renderCard(base);
    expect(screen.queryByTestId("race-card-ev")).toBeNull();
  });

  it("says 未計算 for a race the day listing does not contain", () => {
    renderCard(base, null);
    expect(screen.getByLabelText("期待回収率の状態")).toHaveTextContent("期待回収率 未計算");
  });

  it("shows the compute time (JST, time only on the race day) and both counts", () => {
    renderCard(base, marketEvDayItemFixture({
      computed_at: "2025-10-11T00:15:00Z", n_exceeds_threshold: 1, n_exceeds_mix_threshold: 3,
    }));
    const hint = screen.getByTestId("race-card-ev");
    expect(hint).toHaveTextContent("期待回収率 09:15 計算");
    expect(hint).toHaveTextContent("120%超 1頭");
    expect(hint).toHaveTextContent("混合100%超 3頭");
    expect(hint).not.toHaveTextContent("オッズ変動");
    // no per-horse figure and no buying language leaks into the card
    expect(hint.textContent).not.toMatch(/%\d|×|おすすめ|買うべき|妙味|儲/);
  });

  it("says 混合なし when the mixed column is unavailable; a later odds move is noted only before the result", () => {
    const moved = marketEvDayItemFixture({
      mix_status: "unavailable", n_exceeds_mix_threshold: null, odds_changed_after_compute: true,
      has_results: false,
    });
    // computed on another day than the race → the full JST date-time
    expect(marketEvHint(moved, "2008-06-02")).toBe(
      "期待回収率 2008/06/01 10:15 計算 · 120%超 1頭 · 混合なし · 計算後にオッズ変動",
    );
    // after the result the stored odds are the official final ones: the move is not news
    expect(marketEvHint({ ...moved, has_results: true }, "2008-06-01")).toBe(
      "期待回収率 10:15 計算 · 120%超 1頭 · 混合なし",
    );
  });

  it("maps the unavailable reasons to neutral text without counts", () => {
    const unavailable = (reason: "field_changed" | "odds_unavailable") =>
      marketEvDayItemFixture({
        status: "unavailable", reason, computed_at: null, odds_observed_at: null,
        odds_changed_after_compute: null, result_pending_at_compute: null, n_horses: null,
        n_exceeds_threshold: null, mix_status: null, n_exceeds_mix_threshold: null,
      });
    expect(marketEvHint(unavailable("field_changed"))).toBe(
      "期待回収率 再計算待ち(出走馬が変わりました)",
    );
    expect(marketEvHint(unavailable("odds_unavailable"))).toBe("期待回収率 単勝オッズ未取得");
  });
});
