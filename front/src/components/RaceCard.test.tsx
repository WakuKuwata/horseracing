import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { RaceSummary } from "../api/types";
import { RaceCard } from "./RaceCard";

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

function renderCard(race: RaceSummary) {
  return render(
    <MemoryRouter>
      <RaceCard race={race} />
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
