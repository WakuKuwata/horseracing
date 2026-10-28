import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../tests/server";
import {
  HttpResponse,
  attentionDayFixture,
  attentionDayItemFixture,
  http,
  marketEvDayFixture,
  marketEvDayItemFixture,
  racePage,
  stageDetail,
} from "../tests/fixtures";
import { renderWithProviders } from "../tests/utils";
import { RaceListPage } from "./RaceListPage";

const BASE = "*/api/v1";

// Feature 138: the page also reads the day's 注目条件 list; default = an empty day (echoes date).
beforeEach(() => {
  server.use(
    http.get(`${BASE}/attention/day`, ({ request }) =>
      HttpResponse.json(
        attentionDayFixture(new URL(request.url).searchParams.get("date") ?? "", []),
      ),
    ),
  );
});

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

  it("lists the day's 注目条件 horses below the board, in the API's post order (138)", async () => {
    const dates: string[] = [];
    server.use(
      http.get(`${BASE}/races`, () => HttpResponse.json(racePage)),
      http.get(`${BASE}/attention/day`, ({ request }) => {
        const date = new URL(request.url).searchParams.get("date") ?? "";
        dates.push(date);
        return HttpResponse.json(
          attentionDayFixture(date, [
            attentionDayItemFixture({ horse_id: "h1", horse_number: 1, horse_name: "イチ" }),
            attentionDayItemFixture({
              race_id: "200806010112", race_number: 12, post_time: null,
              horse_id: "h9", horse_number: 9, horse_name: "キュウ",
              chip_rule: "S4", chip_stage: stageDetail("failed", 300),
            }),
          ]),
        );
      }),
    );
    renderWithProviders(<RaceListPage />);
    const list = await screen.findByTestId("attention-day-list");
    await within(list).findByText("イチ");
    // the effective date (latest race day) is what the list asks for
    expect(dates).toContain("2008-06-01");
    const rows = Array.from(list.querySelectorAll("tbody tr"));
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "attention-day-row-200806010111-h1",
      "attention-day-row-200806010112-h9",
    ]);
    expect(within(list).getByRole("note", { name: "注目条件 S1・研究中" })).toBeInTheDocument();
    expect(within(list).getByRole("note", { name: "注目条件 S4・300 点不通過" })).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText("発走時刻不明")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "東京 11R" })).toHaveAttribute(
      "href",
      "/races/200806010111",
    );
    // the board stays the primary content above the list
    const board = screen.getByText("東京", { selector: ".venue-group__title *, .venue-group__title" });
    expect(board.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("an empty attention day reads 「該当なし」 without an error state (138)", async () => {
    server.use(http.get(`${BASE}/races`, () => HttpResponse.json(racePage)));
    renderWithProviders(<RaceListPage />);
    expect(await screen.findByTestId("attention-day-empty")).toHaveTextContent("該当なし");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not show the attention list when there is no race day at all (138)", async () => {
    server.use(
      http.get(`${BASE}/races`, () => HttpResponse.json({ ...racePage, items: [], total: 0 })),
    );
    renderWithProviders(<RaceListPage />);
    expect(await screen.findByText("レースデータがありません")).toBeInTheDocument();
    expect(screen.queryByTestId("attention-day-list")).toBeNull();
  });

  // --- B7: the day's 期待回収率 at a glance -------------------------------------------------------

  it("shows the day's 期待回収率 summary above the board and the hint on each card (B7)", async () => {
    const dates: string[] = [];
    const twoRaces = {
      ...racePage,
      items: [racePage.items[0], { ...racePage.items[0], race_id: "200806010112", race_number: 12 }],
      total: 2,
    };
    server.use(
      http.get(`${BASE}/races`, () => HttpResponse.json(twoRaces)),
      http.get(`${BASE}/market-ev/day`, ({ request }) => {
        const date = new URL(request.url).searchParams.get("date") ?? "";
        dates.push(date);
        return HttpResponse.json(
          marketEvDayFixture(date, [marketEvDayItemFixture({ n_exceeds_threshold: 0 })], 2),
        );
      }),
    );
    renderWithProviders(<RaceListPage />);
    const summary = await screen.findByTestId("market-ev-day-summary");
    expect(dates).toContain("2008-06-01");
    expect(summary).toHaveTextContent("計算済み 1 / 2 レース");
    expect(summary).toHaveTextContent("120%超の馬がいるレース 0");
    expect(summary).toHaveTextContent("市場混合100%超の馬がいるレース 1");
    expect(summary).toHaveTextContent("期待回収率では並べ替えません");
    // the summary sits above the board; the board order is untouched (11R before 12R)
    const board = screen.getByText("東京", { selector: ".venue-group__title *, .venue-group__title" });
    expect(summary.compareDocumentPosition(board) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const hints = screen.getAllByTestId("race-card-ev");
    expect(hints.map((h) => h.textContent)).toEqual([
      "期待回収率 10:15 計算 · 120%超 0頭 · 混合100%超 3頭",
      "期待回収率 未計算",
    ]);
    const links = screen.getAllByRole("link", { name: /R/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/races/200806010111",
      "/races/200806010112",
    ]);
  });

  it("a failed 期待回収率 listing is a quiet note: the board stays and the cards show no hint (B7)", async () => {
    server.use(
      http.get(`${BASE}/races`, () => HttpResponse.json(racePage)),
      http.get(`${BASE}/market-ev/day`, () =>
        HttpResponse.json({ status: 500, code: "internal", detail: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(<RaceListPage />);
    expect(await screen.findByTestId("market-ev-day-error")).toHaveTextContent("HTTP 500 internal");
    expect(screen.getByText("11R")).toBeInTheDocument();
    expect(screen.queryByTestId("race-card-ev")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
