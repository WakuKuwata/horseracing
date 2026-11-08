import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { happyHandlers, http, HttpResponse, marketEvAvailable, predictionResponse, raceDetail } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { RaceDetailPage } from "./RaceDetailPage";

const RACE = "200806010111";
const KEY = `horseracing:forecast-marks:v1:${RACE}`;

function renderForecast() {
  return renderWithProviders(<>
    <Link to="/races/200806010112">次のレース</Link>
    <Link to={`/races/${RACE}`}>元のレース</Link>
    <Routes><Route path="/races/:raceId" element={<RaceDetailPage />} /></Routes>
  </>, { route: `/races/${RACE}` });
}

beforeEach(() => {
  // Node 25's native Web Storage shadows jsdom's implementation in this Vitest version.
  // Keep a working Storage scoped to this suite, as in the existing budget tests.
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, String(value)); },
    removeItem: (key: string) => { store.delete(key); },
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() { return store.size; },
  } satisfies Storage);
  server.use(
    http.get("*/api/v1/purchase-records", () => HttpResponse.json({ records: [] })),
    http.get("*/api/v1/races/:id", () => HttpResponse.json({
      ...raceDetail,
      horses: [
        { horse_id: "h2", horse_number: 2, entry_status: "started", horse_name: "ニ", odds: 5.4 },
        { horse_id: "h1", horse_number: 1, entry_status: "started", horse_name: "イチ", odds: 3.1 },
        { horse_id: "h3", horse_number: 3, entry_status: "scratched", horse_name: "取消馬" },
      ],
    })),
    http.get("*/api/v1/races/:id/market-ev", () => HttpResponse.json(marketEvAvailable)),
    http.get("*/api/v1/races/:id/predictions", () => HttpResponse.json({
      ...predictionResponse,
      horses: [
        { ...predictionResponse.horses[1], top2: 0.65, top3: 0.8 },
        predictionResponse.horses[0],
        { ...predictionResponse.horses[0], horse_id: "h3", horse_number: 3, win: 0.9 },
      ],
    })),
    ...happyHandlers,
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("main forecast table", () => {
  it("compares probabilities and ROI by horse identity, sorts each rate descending and keeps marks attached", async () => {
    renderForecast();
    const table = await screen.findByRole("table", { name: "予想の出走表" });
    await within(table).findByText("32.0%");
    const firstHorse = within(table).getByRole("link", { name: "イチ" }).closest("tr")!;
    // 連対/複勝 are stacked under the 勝率 in ONE column (same cell as the comparison tab)
    expect(within(firstHorse).getByText("連対 55%")).toBeInTheDocument();
    expect(within(firstHorse).getByText("複勝 70%")).toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: /^(連対率|複勝率)/ })).toBeNull();
    expect(await within(firstHorse).findByText("123.7%")).toBeInTheDocument();
    expect(screen.getByTestId("forecast-model-info")).toHaveTextContent("lgbm-006");
    expect(screen.getByTestId("forecast-model-info")).toHaveTextContent("計算日時");
    expect(within(table).queryByText(/市場評価|市場との差|寄与/)).toBeNull();

    await userEvent.selectOptions(within(firstHorse).getByRole("combobox"), "◎");
    await userEvent.click(within(table).getByRole("button", { name: /^モデル勝率/ }));
    expect(within(within(table).getAllByRole("row")[1]).getByRole("link", { name: "イチ" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: /^モデル勝率/ })).toHaveAttribute("aria-sort", "descending");
    expect(within(table).getByRole("combobox", { name: "1番 イチの自分の印" })).toHaveValue("◎");
    expect(within(table).getByRole("combobox", { name: "3番 取消馬の自分の印" })).toBeDisabled();
    expect(within(table).queryByText("90.0%")).toBeNull();
  });

  it("restores marks on reload, isolates races and removes a cleared mark", async () => {
    const view = renderForecast();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "1番 イチの自分の印" }), "◎");
    expect(JSON.parse(window.localStorage.getItem(KEY)!).marks).toEqual({ h1: "◎" });
    await userEvent.click(screen.getByRole("link", { name: "次のレース" }));
    expect(await screen.findByRole("combobox", { name: "1番 イチの自分の印" })).toHaveValue("");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "1番 イチの自分の印" }), "○");
    await userEvent.click(screen.getByRole("link", { name: "元のレース" }));
    expect(await screen.findByRole("combobox", { name: "1番 イチの自分の印" })).toHaveValue("◎");
    view.unmount();
    renderForecast();
    expect(await screen.findByRole("combobox", { name: "1番 イチの自分の印" })).toHaveValue("◎");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "1番 イチの自分の印" }), "");
    expect(JSON.parse(window.localStorage.getItem(KEY)!).marks).toEqual({});
  });

  it.each([404, 503])("keeps ROI, probability columns and mark editing available when predictions return %i", async (status) => {
    server.use(http.get("*/api/v1/races/:id/predictions", () => HttpResponse.json(
      { status, code: "prediction_unavailable", detail: "unavailable" }, { status },
    )));
    renderForecast();
    await screen.findByText(status === 404 ? /確率予測は未生成/ : /確率予測を取得できませんでした（HTTP/);
    const table = screen.getByRole("table", { name: "予想の出走表" });
    expect(await within(table).findByText("123.7%")).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: /^モデル勝率/ })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "1番 イチの自分の印" }), "☆");
    expect(JSON.parse(window.localStorage.getItem(KEY)!).marks.h1).toBe("☆");
  });

  it("preserves the user's mark when refreshed prediction values change", async () => {
    const { queryClient } = renderForecast();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "1番 イチの自分の印" }), "▲");
    server.use(http.get("*/api/v1/races/:id/predictions", () => HttpResponse.json({
      ...predictionResponse,
      horses: [{ ...predictionResponse.horses[0], win: 0.45 }],
    })));
    await queryClient.invalidateQueries({ queryKey: ["predictions", RACE] });
    await within(screen.getByRole("table", { name: "予想の出走表" })).findByText("45.0%");
    expect(screen.getByRole("combobox", { name: "1番 イチの自分の印" })).toHaveValue("▲");
  });

  it("retains a cancelled horse's saved mark without allowing edits", async () => {
    window.localStorage.setItem(KEY, JSON.stringify({ version: 1, marks: { h3: "△" } }));
    renderForecast();
    const mark = await screen.findByRole("combobox", { name: "3番 取消馬の自分の印" });
    expect(mark).toHaveValue("△");
    expect(mark).toBeDisabled();
    expect(JSON.parse(window.localStorage.getItem(KEY)!).marks.h3).toBe("△");
  });

  it("handles invalid stored data and recovers after a new selection", async () => {
    window.localStorage.setItem(KEY, JSON.stringify({ version: 1, marks: { h1: "invalid" } }));
    renderForecast();
    await screen.findByText(/保存した印を読み込めませんでした/);
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "1番 イチの自分の印" }), "△");
    expect(screen.queryByText(/保存した印を読み込めませんでした/)).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(KEY)!).marks).toEqual({ h1: "△" });
  });

  it("keeps the current mark visible and explains persistence failure", async () => {
    const write = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("quota"); });
    renderForecast();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "1番 イチの自分の印" }), "◎");
    expect(screen.getByRole("combobox", { name: "1番 イチの自分の印" })).toHaveValue("◎");
    expect(screen.getByRole("status")).toHaveTextContent("再読み込みすると失われます");
    write.mockRestore();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "2番 ニの自分の印" }), "○");
    await waitFor(() => expect(screen.queryByText(/印をこのブラウザに保存できませんでした/)).toBeNull());
    expect(JSON.parse(window.localStorage.getItem(KEY)!).marks).toEqual({ h1: "◎", h2: "○" });
  });
});
