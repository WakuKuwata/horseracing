import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type {
  HorseEntry,
  HorsePrediction,
  MarketEvAvailable,
  MarketEvUnavailable,
} from "../api/types";
import { EXPECTED_RETURN_SCOPE, PROFIT_COLOUR_SELECTOR } from "../lib/forbiddenPhrases";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { renderWithProviders } from "../tests/utils";
import { HorseEntriesTable } from "./HorseEntriesTable";

const entries: HorseEntry[] = [
  { horse_id: "2020000001", horse_name: "本登録馬", horse_number: 1, entry_status: "started",
    jockey_id: "05339", jockey_name: "本登録騎手" },
  // surrogate (nk:) entities still resolve to a profile -> linked too (colon kept in the path)
  { horse_id: "nk:99999", horse_name: "サロゲート馬", horse_number: 2, entry_status: "started",
    jockey_id: "nk:88", jockey_name: "サロゲート騎手" },
  // a missing jockey_id -> plain text, no link
  { horse_id: "2020000003", horse_name: "騎手なし馬", horse_number: 3, entry_status: "started",
    jockey_name: "未定" },
];

const predictions: HorsePrediction[] = [
  { horse_id: "2020000001", horse_number: 1, win: 0.32, top2: 0.55, top3: 0.7,
    market_win_prob: 0.3, prior_starts_band: "many", divergence: "model_higher",
    explanation: null },
  { horse_id: "nk:99999", horse_number: 2, win: 0.18, top2: 0.4, top3: 0.58,
    market_win_prob: 0.2, prior_starts_band: "few", divergence: null, explanation: null },
  // top2/top3 absent -> the 連対/複勝 sub-line is omitted entirely (no placeholder noise)
  { horse_id: "2020000003", horse_number: 3, win: 0.05, market_win_prob: null,
    prior_starts_band: null, divergence: null, explanation: null },
];

describe("HorseEntriesTable profile links (029)", () => {
  it("links horse/jockey names (incl. nk: surrogates) and skips only null ids", () => {
    renderWithProviders(<HorseEntriesTable entries={entries} predictions={[]} />);
    // canonical ids -> links
    expect(screen.getByRole("link", { name: "本登録馬" })).toHaveAttribute(
      "href", "/horses/2020000001",
    );
    expect(screen.getByRole("link", { name: "本登録騎手" })).toHaveAttribute(
      "href", "/jockeys/05339",
    );
    // surrogate ids -> also linked (they resolve to a profile)
    expect(screen.getByRole("link", { name: "サロゲート馬" })).toHaveAttribute(
      "href", "/horses/nk:99999",
    );
    expect(screen.getByRole("link", { name: "サロゲート騎手" })).toHaveAttribute(
      "href", "/jockeys/nk:88",
    );
    // null jockey_id -> plain text, no link
    expect(screen.queryByRole("link", { name: "未定" })).toBeNull();
    expect(screen.getByText("未定")).toBeInTheDocument();
  });

  it("hides prediction columns/sub-lines entirely when no prediction run exists", () => {
    renderWithProviders(<HorseEntriesTable entries={entries} predictions={[]} />);
    expect(screen.queryByText("モデル勝率")).toBeNull();
    expect(screen.queryByText(/市場評価/)).toBeNull();
    expect(screen.queryByText("市場との差")).toBeNull();
  });
});

// Ported from PQCompare (021 SC-001/002/007) when the p/q comparison merged into this table.
describe("HorseEntriesTable p/q presentation (021 invariants)", () => {
  it("shows 市場評価 INSIDE the 単勝 cell (labelled sub-line, tooltip disclosure); p unbadged", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable
        entries={entries}
        predictions={predictions}
        canonicalConsistent={true}
      />,
    );
    expect(screen.getByText("32.0%")).toBeInTheDocument();
    // q renders as a labelled sub-line of the 単勝 cell (same column as its source odds)
    const qLine = screen.getByText("市場評価 30.0%");
    const oddsHeader = screen.getByText(/^単勝/).closest("th");
    // the pseudo disclosure lives in the 単勝 header tooltip (user decision 2026-07-02:
    // badges stretched the column) + the always-visible note under the table (RaceDetailPage)
    expect(oddsHeader?.getAttribute("title")).toMatch(/市場評価/);
    expect(oddsHeader?.getAttribute("title")).toMatch(/実測ではありません/);
    expect(qLine).toBeInTheDocument();
    // model p column carries no pseudo wording in its tooltip
    const pHeader = screen.getByText(/^モデル勝率/).closest("th");
    expect(pHeader?.getAttribute("title") ?? "").not.toMatch(/推定値/);
    expect(screen.getByText("32.0%").closest('[data-pseudo="true"]')).toBeNull();
    // no separate 市場評価 column header — it lives in the 単勝 cell
    const headers = Array.from(container.querySelectorAll("thead th")).map(
      (th) => th.textContent?.replace(/ [▲▼]$/, "") ?? "",
    );
    expect(headers).not.toContain("市場評価");
    const oddsIdx = headers.indexOf("単勝");
    expect(headers[oddsIdx + 1]).toBe("モデル勝率");
    // q missing (horse 3) -> the 市場評価 sub-line is omitted entirely, never 0
    const rows = container.querySelectorAll("tbody tr");
    expect(rows[2].textContent).not.toContain("市場評価");
  });

  it("stacks 連対/複勝 vertically as separate sub-lines under モデル勝率", () => {
    renderWithProviders(
      <HorseEntriesTable
        entries={entries}
        predictions={predictions}
        canonicalConsistent={true}
      />,
    );
    // horse 1: win 32%, top2 55%, top3 70% — each cumulative prob on its own line
    const top2 = screen.getByText("連対 55%");
    const top3 = screen.getByText("複勝 70%");
    expect(top2).toBeInTheDocument();
    expect(top3).toBeInTheDocument();
    expect(top2.closest("td")).toBe(top3.closest("td"));
    // sub-lines are block elements (vertical stack), not one combined line
    expect(top2.className).toContain("cell-sub");
    expect(top3.className).toContain("cell-sub");
    // horse 3 has no top2/top3 -> no placeholder noise
    expect(screen.queryByText(/連対 —/)).toBeNull();
  });

  it("shows 市場との差 as a NON-sortable column with divergence-band colour, neutral wording", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable
        entries={entries}
        predictions={predictions}
        canonicalConsistent={true}
      />,
    );
    const diffHeader = screen.getByText("市場との差");
    // the diff column must not participate in sorting (edge-sort prohibition, 021 R3)
    expect(diffHeader.closest("th")?.className).not.toContain("sortable");
    expect(diffHeader.closest("th")?.getAttribute("aria-sort")).toBeNull();
    const diffCell = screen.getByText("+2.0pt"); // 0.32-0.30
    // divergence band drives a neutral categorical colour class on the value
    expect(diffCell.closest("td")?.className).toContain("diff--model_higher");
    // the tooltip keeps the factual sentence + non-guarantee disclaimer
    expect(diffCell.closest("td")?.getAttribute("title")).toMatch(/保証するものではありません/);
    // no buy/profit wording in the table
    const table = container.querySelector("table");
    expect(table?.textContent).not.toMatch(/買い|お買い得|おすすめ|妙味/);
    // no win/loss colour classes
    expect(container.querySelector(".profit, .good, .bad, .up, .down")).toBeNull();
  });

  it("suppresses 市場との差 when populations differ (canonical_consistent=false)", () => {
    renderWithProviders(
      <HorseEntriesTable
        entries={entries}
        predictions={predictions}
        canonicalConsistent={false}
      />,
    );
    expect(screen.queryByText("市場との差")).toBeNull();
    // p/q columns still shown — only the mathematically incomparable diff is hidden
    expect(screen.getByText(/^モデル勝率/)).toBeInTheDocument();
  });

  it("sorts by モデル勝率 DESC by default (prediction-first screen)", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable
        entries={entries}
        predictions={[
          // deliberately NOT in win order relative to entries
          { horse_id: "2020000001", horse_number: 1, win: 0.05, market_win_prob: 0.1 },
          { horse_id: "nk:99999", horse_number: 2, win: 0.32, market_win_prob: 0.2 },
          { horse_id: "2020000003", horse_number: 3, win: 0.18, market_win_prob: 0.15 },
        ]}
        canonicalConsistent={true}
      />,
    );
    const winHeader = screen.getByText(/^モデル勝率/).closest("th");
    expect(winHeader?.getAttribute("aria-sort")).toBe("descending");
    // rows ordered 0.32 → 0.18 → 0.05, not entry order
    const names = Array.from(
      container.querySelectorAll("tbody tr td:nth-child(2) .cell-main"),
    ).map((n) => n.textContent);
    expect(names).toEqual(["サロゲート馬", "騎手なし馬", "本登録馬"]);
  });

  it("shows the prior-starts band as a neutral fact next to the horse", () => {
    renderWithProviders(
      <HorseEntriesTable
        entries={entries}
        predictions={predictions}
        canonicalConsistent={true}
      />,
    );
    expect(screen.getByText("出走歴 多")).toBeInTheDocument();
    expect(screen.getByText("出走歴 少")).toBeInTheDocument();
  });
});

// Feature 137: 期待回収率 column (separate market-aware model, pseudo, API-flagged highlight).
describe("HorseEntriesTable 期待回収率 (137)", () => {
  const evEntries: HorseEntry[] = [
    ...entries,
    // a cancelled horse — even with a (stale) stored row it must show "—", never a value
    { horse_id: "2020000004", horse_name: "取消馬", horse_number: 4, entry_status: "cancelled",
      jockey_name: "騎手D" },
  ];

  const marketEv: MarketEvAvailable = {
    status: "available",
    race_id: "202609270511",
    model_version: "mev-binary-v2",
    logic_version: "mev-v1",
    computed_at: "2026-09-27T00:15:00Z",
    odds_observed_at: "2026-09-27T00:10:00Z",
    odds_changed_after_compute: false,
    result_pending_at_compute: true,
    threshold: 1.2,
    is_pseudo: true,
    horses: [
      { horse_id: "2020000001", horse_number: 1, expected_return: 1.243, odds_used: 3.9,
        exceeds_threshold: true },
      // exactly the threshold: the API says NOT exceeding (strict >) → no highlight
      { horse_id: "nk:99999", horse_number: 2, expected_return: 1.2, odds_used: 6.0,
        exceeds_threshold: false },
      // horse 3 (2020000003) has no stored row → "—"
      { horse_id: "2020000004", horse_number: 4, expected_return: 1.5, odds_used: 12.0,
        exceeds_threshold: true },
    ],
  };

  const notComputed: MarketEvUnavailable = {
    status: "unavailable", race_id: "202609270511", reason: "not_computed", threshold: 1.2,
  };

  function rowOf(container: HTMLElement, name: string): HTMLTableRowElement {
    const row = Array.from(container.querySelectorAll<HTMLTableRowElement>("tbody tr")).find(
      (tr) => tr.querySelector("td:nth-child(2) .cell-main")?.textContent?.startsWith(name),
    );
    if (!row) throw new Error(`row ${name} not found`);
    return row;
  }

  function headerTexts(container: HTMLElement): string[] {
    return Array.from(container.querySelectorAll("thead th")).map(
      (th) => th.textContent?.replace(/ [▲▼]$/, "") ?? "",
    );
  }

  function evCell(row: HTMLTableRowElement): HTMLTableCellElement {
    const cell = row.querySelector<HTMLTableCellElement>("td.ev-cell");
    if (!cell) throw new Error("ev cell not found");
    return cell;
  }

  it("renders every value as a badged pseudo 推定 figure (coverage, not a spot-check)", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={evEntries} predictions={predictions}
        canonicalConsistent={true} marketEv={marketEv} />,
    );
    assertPseudoLabelCoverage(container, ["124.3%", "120.0%"]);
    const nodes = container.querySelectorAll('[data-pseudo-kind="expected_return"]');
    expect(nodes).toHaveLength(2);
    nodes.forEach((n) => expect(n.querySelector("[data-pseudo-badge]")).toHaveTextContent("推定"));
  });

  it("highlights only the rows the API flags (1.2 exactly is NOT flagged)", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={evEntries} predictions={predictions}
        canonicalConsistent={true} marketEv={marketEv} />,
    );
    const over = rowOf(container, "本登録馬");
    expect(over.className).toContain("entry--ev-over");
    const chip = within(over).getByLabelText("期待回収率が120%を超えています");
    expect(chip).toHaveTextContent("120%超");
    expect(chip.getAttribute("title")).toBe("期待回収率が120%を超えています");
    expect(evCell(over)).toHaveTextContent("124.3%");

    const exact = rowOf(container, "サロゲート馬");
    expect(evCell(exact)).toHaveTextContent("120.0%");
    expect(exact.className).not.toContain("entry--ev-over");
    expect(within(exact).queryByText(/超$/)).toBeNull();
    // exactly one highlighted row in the whole table
    expect(container.querySelectorAll("tr.entry--ev-over")).toHaveLength(1);
  });

  it("follows the API's exceeds_threshold flag and never recomputes it from the value", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={entries} predictions={[]} marketEv={{
        ...marketEv,
        horses: [
          { horse_id: "2020000001", horse_number: 1, expected_return: 1.25, odds_used: 5.0,
            exceeds_threshold: false },
        ],
      }} />,
    );
    const row = rowOf(container, "本登録馬");
    expect(evCell(row)).toHaveTextContent("125.0%");
    expect(row.className).not.toContain("entry--ev-over");
    expect(screen.queryByLabelText(/を超えています/)).toBeNull();
  });

  it("shows — for a cancelled horse and a horse without a stored row (never 0/NaN)", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={evEntries} predictions={predictions}
        canonicalConsistent={true} marketEv={marketEv} />,
    );
    const cancelled = rowOf(container, "取消馬");
    expect(evCell(cancelled).textContent).toBe("—");
    expect(cancelled.className).not.toContain("entry--ev-over");
    expect(evCell(cancelled).querySelector("[data-pseudo]")).toBeNull();

    const missing = rowOf(container, "騎手なし馬");
    expect(evCell(missing).textContent).toBe("—");
    container.querySelectorAll("td.ev-cell").forEach((td) => {
      expect(td.textContent).not.toMatch(/NaN|undefined/);
      expect(td.textContent).not.toMatch(/^0\.0%/);
    });
  });

  it("is NOT sortable and sits right after モデル勝率", async () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={evEntries} predictions={predictions}
        canonicalConsistent={true} marketEv={marketEv} />,
    );
    const headers = headerTexts(container);
    expect(headers[headers.indexOf("モデル勝率") + 1]).toBe("期待回収率");
    const evHeader = screen.getByText("期待回収率").closest("th");
    expect(evHeader?.className).not.toContain("sortable");
    expect(evHeader?.getAttribute("aria-sort")).toBeNull();
    expect(evHeader?.getAttribute("title")).toMatch(/別の市場連動モデル/);

    const namesBefore = Array.from(
      container.querySelectorAll("tbody tr td:nth-child(2) .cell-main"),
    ).map((n) => n.textContent);
    await userEvent.click(evHeader!);
    const namesAfter = Array.from(
      container.querySelectorAll("tbody tr td:nth-child(2) .cell-main"),
    ).map((n) => n.textContent);
    // clicking the header changes nothing: the default モデル勝率 sort stays in force
    expect(namesAfter).toEqual(namesBefore);
    const winHeader = screen.getByText(/^モデル勝率/).closest("th");
    expect(winHeader?.getAttribute("aria-sort")).toBe("descending");
  });

  it("is shown without predictions (right after 単勝) — independent of the win model", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={entries} predictions={[]} marketEv={marketEv} />,
    );
    const headers = headerTexts(container);
    expect(headers).not.toContain("モデル勝率");
    expect(headers[headers.indexOf("単勝") + 1]).toBe("期待回収率");
    expect(headers[headers.length - 1]).toBe("期待回収率");
    expect(screen.getByText("124.3%")).toBeInTheDocument();
  });

  it.each([
    ["unavailable", notComputed],
    ["undefined", undefined],
    ["null", null],
  ])("hides the column entirely when marketEv is %s", (_label, value) => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={entries} predictions={predictions}
        canonicalConsistent={true} marketEv={value} />,
    );
    expect(screen.queryByText("期待回収率")).toBeNull();
    expect(container.querySelector("td.ev-cell")).toBeNull();
    expect(container.querySelector("tr.entry--ev-over")).toBeNull();
  });

  it("widens the expansion row colSpan to cover the new column", async () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={evEntries} predictions={predictions}
        canonicalConsistent={true} marketEv={marketEv} />,
    );
    const nHeaders = container.querySelectorAll("thead th").length;
    await userEvent.click(within(rowOf(container, "本登録馬")).getByRole("button",
      { name: "スコア寄与" }));
    const expansion = container.querySelector<HTMLTableCellElement>("tr.explanation-row td");
    expect(expansion?.colSpan).toBe(nHeaders);
    // every body row has exactly as many cells as there are headers
    container.querySelectorAll("tbody tr.entry-row").forEach((tr) =>
      expect(tr.querySelectorAll("td")).toHaveLength(nHeaders),
    );
  });

  it("uses no buy wording, no scoped forbidden phrase and no profit/loss colour", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={evEntries} predictions={predictions}
        canonicalConsistent={true} marketEv={marketEv} />,
    );
    const table = container.querySelector("table")!;
    expect(table.textContent).not.toMatch(/買/);
    expect(table.textContent).not.toMatch(EXPECTED_RETURN_SCOPE);
    const attrs = Array.from(table.querySelectorAll("[title], [aria-label]")).flatMap((el) => [
      el.getAttribute("title") ?? "",
      el.getAttribute("aria-label") ?? "",
    ]);
    for (const a of attrs) {
      expect(a).not.toMatch(/買/);
      expect(a).not.toMatch(EXPECTED_RETURN_SCOPE);
    }
    expect(container.querySelector(`${PROFIT_COLOUR_SELECTOR}, .up, .down`)).toBeNull();
  });
});
