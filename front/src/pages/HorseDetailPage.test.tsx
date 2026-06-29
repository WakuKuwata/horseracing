import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { HorseDetailPage } from "./HorseDetailPage";

const BASE = "*/api/v1";

function profile(over: Record<string, unknown> = {}) {
  return {
    horse_id: "H1", horse_name: "テスト馬", sex: "牡", birth_year: 2020,
    sire_name: "父S", dam_name: "母D", damsire_name: "母父D",
    starts: 4, wins: 1, seconds_in: 2, shows_in: 2,
    win_rate: 0.25, quinella_rate: 0.5, show_rate: 0.5, avg_finish: 2.67,
    ...over,
  };
}

function historyPage(items: unknown[]) {
  return { items, page: 1, page_size: 50, total: items.length, has_next: false };
}

function renderHorse() {
  return renderWithProviders(
    <Routes>
      <Route path="/horses/:horseId" element={<HorseDetailPage />} />
    </Routes>,
    { route: "/horses/H1" },
  );
}

describe("HorseDetailPage", () => {
  it("renders identity, pedigree names and career stats", async () => {
    server.use(
      http.get(`${BASE}/horses/H1`, () => HttpResponse.json(profile())),
      http.get(`${BASE}/horses/H1/history`, () =>
        HttpResponse.json(
          historyPage([
            { race_id: "200806010101", race_date: "2008-06-01", venue_code: "05",
              race_number: 11, race_name: "テストS", finish_order: 1, popularity: 1, odds: 2.0,
              last_3f: 34.5, entry_status: "started", result_status: "finished" },
          ]),
        ),
      ),
    );
    renderHorse();
    expect(await screen.findByText("テスト馬")).toBeInTheDocument();
    expect(screen.getByText("父 父S")).toBeInTheDocument();
    expect(screen.getByText("25.0%")).toBeInTheDocument(); // win_rate
    // history row links to the race
    expect(await screen.findByRole("link", { name: "テストS" })).toHaveAttribute(
      "href", "/races/200806010101",
    );
  });

  it("shows a typed error state when the profile 404s", async () => {
    server.use(
      http.get(`${BASE}/horses/H1`, () =>
        HttpResponse.json({ status: 404, code: "horse_not_found", detail: "horse H1 not found" },
          { status: 404 }),
      ),
      http.get(`${BASE}/horses/H1/history`, () =>
        HttpResponse.json({ status: 404, code: "horse_not_found", detail: "x" }, { status: 404 }),
      ),
    );
    renderHorse();
    // both panels (profile + history) surface the typed error
    expect((await screen.findAllByText(/エラー 404/)).length).toBeGreaterThan(0);
  });

  it("shows nullable rates as — and an empty history state for a debut horse", async () => {
    server.use(
      http.get(`${BASE}/horses/H1`, () =>
        HttpResponse.json(profile({
          starts: 0, wins: 0, seconds_in: 0, shows_in: 0,
          win_rate: null, quinella_rate: null, show_rate: null, avg_finish: null,
        })),
      ),
      http.get(`${BASE}/horses/H1/history`, () => HttpResponse.json(historyPage([]))),
    );
    renderHorse();
    expect(await screen.findByText("テスト馬")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0); // null rates render as em-dash
    expect(await screen.findByText("出走履歴がありません")).toBeInTheDocument();
  });
});
