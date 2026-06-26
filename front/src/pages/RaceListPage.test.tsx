import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { server } from "../tests/server";
import { HttpResponse, http, racePage } from "../tests/fixtures";
import { renderWithProviders } from "../tests/utils";
import { RaceListPage } from "./RaceListPage";

const BASE = "*/api/v1";

describe("RaceListPage", () => {
  it("renders the race table on success", async () => {
    server.use(http.get(`${BASE}/races`, () => HttpResponse.json(racePage)));
    renderWithProviders(<RaceListPage />);
    expect(await screen.findByText("200806010111")).toBeInTheDocument();
    expect(screen.getByText(/1–1 \/ 1 件/)).toBeInTheDocument();
  });

  it("shows the empty state (distinct from error) on 200 with no rows", async () => {
    server.use(
      http.get(`${BASE}/races`, () =>
        HttpResponse.json({ ...racePage, items: [], total: 0 }),
      ),
    );
    renderWithProviders(<RaceListPage />);
    expect(await screen.findByText("該当するレースがありません")).toBeInTheDocument();
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
