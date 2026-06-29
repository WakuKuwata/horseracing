import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { JockeyDetailPage } from "./JockeyDetailPage";

const BASE = "*/api/v1";

function renderJockey() {
  return renderWithProviders(
    <Routes>
      <Route path="/jockeys/:jockeyId" element={<JockeyDetailPage />} />
    </Routes>,
    { route: "/jockeys/J1" },
  );
}

describe("JockeyDetailPage", () => {
  it("renders riding stats and mount history", async () => {
    server.use(
      http.get(`${BASE}/jockeys/J1`, () =>
        HttpResponse.json({
          jockey_id: "J1", jockey_name: "テスト騎手", mounts: 10, wins: 3,
          seconds_in: 5, shows_in: 6, win_rate: 0.3, quinella_rate: 0.5, show_rate: 0.6,
          avg_finish: 4.2,
        }),
      ),
      http.get(`${BASE}/jockeys/J1/history`, () =>
        HttpResponse.json({
          items: [
            { race_id: "200806010101", race_date: "2008-06-01", venue_code: "05",
              race_number: 11, race_name: "テストS", horse_id: "H1", horse_name: "テスト馬",
              finish_order: 1, result_status: "finished" },
          ],
          page: 1, page_size: 50, total: 1, has_next: false,
        }),
      ),
    );
    renderJockey();
    expect(await screen.findByText("テスト騎手")).toBeInTheDocument();
    expect(screen.getByText("30.0%")).toBeInTheDocument(); // win_rate
    // mount horse links to the horse profile
    expect(await screen.findByRole("link", { name: "テスト馬" })).toHaveAttribute(
      "href", "/horses/H1",
    );
  });

  it("shows an empty mount-history state", async () => {
    server.use(
      http.get(`${BASE}/jockeys/J1`, () =>
        HttpResponse.json({
          jockey_id: "J1", jockey_name: "新人", mounts: 0, wins: 0, seconds_in: 0, shows_in: 0,
          win_rate: null, quinella_rate: null, show_rate: null, avg_finish: null,
        }),
      ),
      http.get(`${BASE}/jockeys/J1/history`, () =>
        HttpResponse.json({ items: [], page: 1, page_size: 50, total: 0, has_next: false }),
      ),
    );
    renderJockey();
    expect(await screen.findByText("騎乗履歴がありません")).toBeInTheDocument();
  });
});
