import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HorseEntry, MarketEvAvailable, PredictionResponse } from "../api/types";
import { marketEvAvailable, predictionResponse } from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { renderWithProviders } from "../tests/utils";
import { ForecastSummary } from "./ForecastSummary";

const RACE = "200806010111";
const entries: HorseEntry[] = [
  { horse_id: "h4", horse_number: 4, horse_name: "ヨン", entry_status: "started" },
  { horse_id: "h2", horse_number: 2, horse_name: "ニ", entry_status: "started" },
  { horse_id: "h5", horse_number: 5, horse_name: "取消馬", entry_status: "scratched" },
  { horse_id: "h1", horse_number: 1, horse_name: "イチ", entry_status: "started" },
  { horse_id: "h3", horse_number: 3, horse_name: "サン", entry_status: "started" },
];
const prediction: PredictionResponse = {
  ...predictionResponse,
  horses: [
    { horse_id: "h3", horse_number: 3, win: 0.2, top3: 0.95 },
    { horse_id: "h1", horse_number: 1, win: 0.4, top3: 0.65 },
    { horse_id: "h4", horse_number: 4, win: 0.1, top3: 0.55 },
    { horse_id: "h2", horse_number: 2, win: 0.3, top3: 0.85 },
  ],
};
const market: MarketEvAvailable = {
  ...marketEvAvailable,
  horses: [
    { ...marketEvAvailable.horses[0], horse_id: "h4", horse_number: 4, expected_return: 1.5 },
    { ...marketEvAvailable.horses[0], horse_id: "h2", horse_number: 2, expected_return: 1.1 },
    { ...marketEvAvailable.horses[0], horse_id: "h1", horse_number: 1, expected_return: 0.9 },
    { ...marketEvAvailable.horses[0], horse_id: "h3", horse_number: 3, expected_return: 0.95 },
  ],
};

describe("ForecastSummary", () => {
  it("ranks the saved win/top3 predictions independently by horse identity and excludes cancelled entries", () => {
    const { container } = renderWithProviders(<ForecastSummary raceId={RACE} entries={entries} prediction={prediction} marketEv={market} />);
    const wins = screen.getByRole("region", { name: "勝利候補" });
    const places = screen.getByRole("region", { name: "上位入着候補" });
    expect(within(wins).getAllByRole("link").map((link) => link.textContent)).toEqual(["1番 イチ", "2番 ニ", "3番 サン"]);
    expect(within(places).getAllByRole("link").map((link) => link.textContent)).toEqual(["3番 サン", "2番 ニ", "1番 イチ"]);
    expect(within(wins).getByText("勝率 40.0%")).toBeInTheDocument();
    expect(within(places).getByText("3着以内率 95.0%")).toBeInTheDocument();
    expect(within(wins).getByRole("link", { name: "1番 イチ" })).toHaveAttribute("href", "/horses/h1");
    expect(screen.queryByText(/取消馬/)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    assertPseudoLabelCoverage(container, ["90.0%", "110.0%", "95.0%", "150.0%"]);
  });

  it("explains exact rank and ROI display categories without deriving a blended score", () => {
    renderWithProviders(<ForecastSummary raceId={RACE} entries={entries} prediction={prediction} marketEv={market} />);
    const comparison = screen.getByRole("region", { name: "配当との比較" });
    expect(within(comparison).getAllByRole("link").map((link) => link.textContent)).toEqual(["1番 イチ", "4番 ヨン"]);
    expect(within(comparison).getByTitle("勝率上位3頭・期待回収率100%以下のうち勝率が最も高い馬")).toBeInTheDocument();
    expect(within(comparison).getByTitle("勝率上位3頭以外・期待回収率100%超のうち期待回収率が最も高い馬")).toBeInTheDocument();
    expect(within(comparison).getByText("勝率 10.0%")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "自動予想サマリー" })).toHaveTextContent("別モデルの推定");
    expect(screen.getByRole("region", { name: "自動予想サマリー" })).toHaveTextContent("購入推奨ではありません");
  });

  it("orders ties by horse number rather than API or entry order", () => {
    renderWithProviders(<ForecastSummary raceId={RACE} entries={entries} prediction={{
      ...prediction, horses: prediction.horses.map((horse) => ({ ...horse, win: 0.25, top3: 0.75 })),
    }} marketEv={market} />);
    for (const name of ["勝利候補", "上位入着候補"]) {
      expect(within(screen.getByRole("region", { name })).getAllByRole("link").map((link) => link.textContent))
        .toEqual(["1番 イチ", "2番 ニ", "3番 サン"]);
    }
  });

  it("keeps ROI visible when probability prediction is unavailable or fails, and never substitutes ROI for probability", () => {
    const view = renderWithProviders(<ForecastSummary raceId={RACE} entries={entries} marketEv={market} />);
    expect(within(screen.getByRole("region", { name: "勝利候補" })).getByText(/確率予測はまだありません/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "配当との比較" })).getByText("150.0%")).toBeInTheDocument();
    expect(screen.queryByText(/勝率上位3頭・期待回収率/)).toBeNull();
    view.rerender(<ForecastSummary raceId={RACE} entries={entries} prediction={prediction} predictionFailed marketEv={market} />);
    expect(within(screen.getByRole("region", { name: "勝利候補" })).getByText(/確率予測を取得できませんでした/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "配当との比較" })).getByText("150.0%")).toBeInTheDocument();
  });

  it("keeps probability candidates visible when ROI fails", () => {
    renderWithProviders(<ForecastSummary raceId={RACE} entries={entries} prediction={prediction} marketEv={market} marketEvFailed />);
    expect(within(screen.getByRole("region", { name: "勝利候補" })).getByText("勝率 40.0%")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "配当との比較" })).getByText(/期待回収率を取得できませんでした/)).toBeInTheDocument();
    expect(screen.queryByText("150.0%")).toBeNull();
  });

  it("does not use results from another race", () => {
    renderWithProviders(<ForecastSummary raceId="another-race" entries={entries} prediction={prediction} marketEv={market} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(within(screen.getByRole("region", { name: "配当との比較" })).getByText(/未計算または再計算待ち/)).toBeInTheDocument();
  });

  it("excludes invalid numeric outputs and discloses incomplete probability coverage before rank comparisons", () => {
    renderWithProviders(<ForecastSummary raceId={RACE} entries={entries} prediction={{
      ...prediction,
      horses: prediction.horses.map((horse) => horse.horse_id === "h1" ? { ...horse, win: Number.NaN, top3: 1.5 } : horse),
    }} marketEv={{
      ...market,
      horses: market.horses.map((horse) => horse.horse_id === "h4" ? { ...horse, expected_return: Infinity } : horse),
    }} />);
    expect(within(screen.getByRole("region", { name: "勝利候補" })).queryByRole("link", { name: "1番 イチ" })).toBeNull();
    expect(within(screen.getByRole("region", { name: "上位入着候補" })).queryByRole("link", { name: "1番 イチ" })).toBeNull();
    const comparison = screen.getByRole("region", { name: "配当との比較" });
    expect(within(comparison).queryByRole("link")).toBeNull();
    expect(within(comparison).getByText(/出走構成に対応する期待回収率の再計算待ち/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "勝利候補" })).getByText(/出走構成に対応する確率の再計算待ち/)).toBeInTheDocument();
  });

  it("discloses timestamp differences, unknown provenance and changed odds", () => {
    const view = renderWithProviders(<ForecastSummary raceId={RACE} entries={entries} prediction={prediction} marketEv={market} />);
    expect(screen.getByTestId("forecast-summary-timing")).toHaveTextContent("同時点の評価ではありません");
    view.rerender(<ForecastSummary raceId={RACE} entries={entries} prediction={{ ...prediction, odds_as_of: null }} marketEv={{ ...market, odds_changed_after_compute: true }} />);
    expect(screen.getByTestId("forecast-summary-timing")).toHaveTextContent("そろっているか確認できません");
    expect(screen.getByTestId("forecast-summary-timing")).toHaveTextContent("計算後に単勝オッズが更新されています");
  });

  it("waits for recomputation after a scratch even when the API has already filtered away that horse", () => {
    const currentEntries = entries.map((horse) => horse.horse_id === "h4" ? { ...horse, entry_status: "scratched" } : horse);
    renderWithProviders(<ForecastSummary raceId={RACE} entries={currentEntries} prediction={{
      ...prediction, horses: prediction.horses.filter((horse) => horse.horse_id !== "h4"),
    }} marketEv={{ ...market, horses: market.horses.filter((horse) => horse.horse_id !== "h4") }} />);
    const wins = screen.getByRole("region", { name: "勝利候補" });
    expect(within(wins).queryByRole("link")).toBeNull();
    expect(within(wins).getByText(/出走構成に対応する確率の再計算待ち/)).toBeInTheDocument();
    const comparison = screen.getByRole("region", { name: "配当との比較" });
    expect(within(comparison).getByText("110.0%")).toBeInTheDocument();
    expect(within(comparison).queryByText(/勝率上位3頭・期待回収率/)).toBeNull();
  });

  it.each([
    { name: "win mass", mutate: (p: PredictionResponse) => ({ ...p, horses: p.horses.map((h) => ({ ...h, win: h.win! * 0.9 })) }) },
    { name: "top3 mass", mutate: (p: PredictionResponse) => ({ ...p, horses: p.horses.map((h) => ({ ...h, top3: h.top3! * 0.9 })) }) },
    { name: "head ordering", mutate: (p: PredictionResponse) => ({ ...p, horses: p.horses.map((h) => ({ ...h, top2: h.win! / 2 })) }) },
    { name: "top2 mass", mutate: (p: PredictionResponse) => ({ ...p, horses: p.horses.map((h) => ({ ...h, top2: h.win! })) }) },
    { name: "duplicate ID", mutate: (p: PredictionResponse) => ({ ...p, horses: [...p.horses, p.horses[0]] }) },
    { name: "surplus cancelled horse", mutate: (p: PredictionResponse) => ({ ...p, horses: [...p.horses, { horse_id: "h5", horse_number: 5, win: 0, top3: 0 }] }) },
    { name: "missing prediction", mutate: (p: PredictionResponse) => ({ ...p, horses: p.horses.slice(1) }) },
  ])("suppresses candidates and rank/ROI comparisons when $name is invalid", ({ mutate }) => {
    renderWithProviders(<ForecastSummary raceId={RACE} entries={entries} prediction={mutate(prediction)} marketEv={market} />);
    expect(within(screen.getByRole("region", { name: "勝利候補" })).queryByRole("link")).toBeNull();
    expect(within(screen.getByRole("region", { name: "上位入着候補" })).queryByRole("link")).toBeNull();
    const comparison = screen.getByRole("region", { name: "配当との比較" });
    expect(within(comparison).getByText("150.0%")).toBeInTheDocument();
    expect(within(comparison).queryByText(/勝率上位3頭・期待回収率/)).toBeNull();
  });

  it.each(["duplicate", "cancelled"])("suppresses outdated ROI when it contains a %s horse row", (kind) => {
    const extra = kind === "duplicate" ? market.horses[0] : { ...market.horses[0], horse_id: "h5", horse_number: 5 };
    renderWithProviders(<ForecastSummary raceId={RACE} entries={entries} prediction={prediction} marketEv={{
      ...market, horses: [...market.horses, extra],
    }} />);
    expect(within(screen.getByRole("region", { name: "勝利候補" })).getByText("勝率 40.0%")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "配当との比較" })).getByText(/出走構成に対応する期待回収率の再計算待ち/)).toBeInTheDocument();
    expect(screen.queryByText("150.0%")).toBeNull();
  });

  it("uses the smaller field size for top2 and top3 probability masses", () => {
    const selected = entries.filter((horse) => horse.horse_id === "h1" || horse.horse_id === "h2");
    renderWithProviders(<ForecastSummary raceId={RACE} entries={selected} prediction={{
      ...prediction,
      horses: [{ horse_id: "h1", win: 0.7, top2: 1, top3: 1 }, { horse_id: "h2", win: 0.3, top2: 1, top3: 1 }],
    }} />);
    expect(within(screen.getByRole("region", { name: "勝利候補" })).getAllByRole("link")).toHaveLength(2);
    expect(within(screen.getByRole("region", { name: "上位入着候補" })).getAllByRole("link")).toHaveLength(2);
  });
});
