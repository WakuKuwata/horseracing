import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import { HttpResponse, http, racePage } from "../tests/fixtures";
import { renderWithProviders } from "../tests/utils";
import { RaceListPage } from "./RaceListPage";

const BASE = "*/api/v1";

describe("RaceListPage", () => {
  it("renders the day board (venue group + race card) on success", async () => {
    server.use(http.get(`${BASE}/races`, () => HttpResponse.json(racePage)));
    renderWithProviders(<RaceListPage />);
    // venue 05 -> 東京, race_number 11 -> "11R"
    expect(await screen.findByText("東京")).toBeInTheDocument();
    expect(screen.getByText("11R")).toBeInTheDocument();
    // card links to the race detail
    expect(screen.getByRole("link", { name: /11R/ })).toHaveAttribute(
      "href",
      "/races/200806010111",
    );
    // result-status badge: fixture has_results=true -> 結果確定
    expect(screen.getByText("結果確定")).toBeInTheDocument();
  });

  it("marks a result-pending race as 結果待ち", async () => {
    const pending = {
      ...racePage,
      items: [{ ...racePage.items[0], has_results: false }],
    };
    server.use(http.get(`${BASE}/races`, () => HttpResponse.json(pending)));
    renderWithProviders(<RaceListPage />);
    expect(await screen.findByText("結果待ち")).toBeInTheDocument();
  });

  it("shows the empty state (distinct from error) on 200 with no rows", async () => {
    server.use(
      http.get(`${BASE}/races`, () =>
        HttpResponse.json({ ...racePage, items: [], total: 0 }),
      ),
    );
    renderWithProviders(<RaceListPage />);
    expect(await screen.findByText("レースデータがありません")).toBeInTheDocument();
  });

  it("shows a typed error state on 500", async () => {
    server.use(
      http.get(`${BASE}/races`, () =>
        HttpResponse.json({ status: 500, code: "internal", detail: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(<RaceListPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/エラー 500/)).toBeInTheDocument();
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });
});
