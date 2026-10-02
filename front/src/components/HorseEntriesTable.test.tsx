import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type {
  AttentionAvailable,
  AttentionHorse,
  HorseEntry,
  HorsePrediction,
  MarketEvAvailable,
  MarketEvUnavailable,
} from "../api/types";
import { EXPECTED_RETURN_SCOPE, PROFIT_COLOUR_SELECTOR } from "../lib/forbiddenPhrases";
import {
  assertAttentionDiscipline,
  assertRoiBasisCoverage,
} from "../tests/attentionDiscipline";
import { assertRoiBasisLabels } from "../tests/attentionScope";
import {
  attentionAvailableFixture,
  attentionHorseFixture,
  attentionNoChipHorseFixture,
  attentionNotComputed,
  attentionRulesFixture,
  evSnapshotFixture,
  http,
  HttpResponse,
  pickStatusFor,
  stageDetail,
  stagesFor,
} from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { server } from "../tests/server";
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

  // Feature 138 (FR-010): 137's 「120%超」 text chip and its exceeds_threshold-driven outline are
  // gone — the 注目条件 chip replaces them and the outline fires only at emphasis level 3.
  it("no longer marks exceeds_threshold rows: no 「120%超」 chip, no outline (138)", () => {
    const { container } = renderWithProviders(
      <HorseEntriesTable entries={evEntries} predictions={predictions}
        canonicalConsistent={true} marketEv={marketEv} />,
    );
    const flagged = rowOf(container, "本登録馬"); // exceeds_threshold: true in the API row
    expect(evCell(flagged)).toHaveTextContent("124.3%");
    expect(evCell(flagged).textContent).not.toMatch(/超/);
    expect(flagged.className).not.toContain("entry--ev-over");
    expect(container.querySelectorAll("tr.entry--ev-over")).toHaveLength(0);
    expect(container.querySelector(".ev-chip")).toBeNull();
    expect(screen.queryByLabelText(/を超えています/)).toBeNull();
    expect(screen.queryByText("120%超")).toBeNull();
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

// Feature 138: 注目条件 chips (judgment-time, from the API) + the breakdown in the expansion row.
describe("HorseEntriesTable 注目条件 (138)", () => {
  const MIN = 60 * 1000;
  // A race that has not started yet: post 15:25 JST, rendered 3 h 10 min before the post.
  const POST = "2026-10-03T06:25:00Z";
  const NOW = new Date(POST).getTime() - (3 * 60 + 10) * MIN;
  const iso = (ms: number) => new Date(ms).toISOString();

  const raceEntries: HorseEntry[] = [
    ...entries,
    { horse_id: "2020000004", horse_name: "取消馬", horse_number: 4, entry_status: "cancelled",
      jockey_name: "騎手D" },
  ];

  const marketEv: MarketEvAvailable = {
    status: "available",
    race_id: "202610030511",
    model_version: "mev-ens15-v1",
    logic_version: "mev-ens15-v1;seeds=1-15",
    computed_at: iso(NOW - 40 * MIN),
    odds_observed_at: iso(NOW - 42 * MIN),
    odds_changed_after_compute: false,
    result_pending_at_compute: true,
    threshold: 1.2,
    is_pseudo: true,
    horses: [
      { horse_id: "2020000001", horse_number: 1, expected_return: 1.246, odds_used: 30.8,
        exceeds_threshold: true },
      { horse_id: "nk:99999", horse_number: 2, expected_return: 0.864, odds_used: 5.4,
        exceeds_threshold: false },
      { horse_id: "2020000003", horse_number: 3, expected_return: 1.31, odds_used: 25.0,
        exceeds_threshold: true },
    ],
  };

  const fieldChanged: MarketEvUnavailable = {
    status: "unavailable", race_id: "202610030511", reason: "field_changed", threshold: 1.2,
  };

  /** An S1 chip horse (S1, S3, S4 + control S5), 研究中, current row 42 min old (freshness 2). */
  function chipHorse(id: string, n: number, overrides: Partial<AttentionHorse> = {}) {
    return attentionHorseFixture({
      horse_id: id,
      horse_number: n,
      current: evSnapshotFixture({
        ens_expected_return: 1.246, odds: 30.8, odds_observed_at: iso(NOW - 42 * MIN),
        computed_at: iso(NOW - 40 * MIN),
      }),
      ...overrides,
    });
  }

  function race(horses: AttentionHorse[]): AttentionAvailable {
    return attentionAvailableFixture({
      race_id: "202610030511",
      post_time: POST,
      has_results: false,
      judged_at: iso(NOW - 6 * 60 * MIN),
      horses,
    });
  }

  function rowOf(container: HTMLElement, name: string): HTMLTableRowElement {
    const row = Array.from(container.querySelectorAll<HTMLTableRowElement>("tbody tr")).find(
      (tr) => tr.querySelector("td:nth-child(2) .cell-main")?.textContent?.startsWith(name),
    );
    if (!row) throw new Error(`row ${name} not found`);
    return row;
  }

  function evCell(row: HTMLTableRowElement): HTMLTableCellElement {
    const cell = row.querySelector<HTMLTableCellElement>("td.ev-cell");
    if (!cell) throw new Error("ev cell not found");
    return cell;
  }

  function mainChips(root: ParentNode): HTMLElement[] {
    return Array.from(root.querySelectorAll<HTMLElement>(".attn-chip:not(.attn-chip--sub)"));
  }

  function renderTable(
    attention: AttentionAvailable | typeof attentionNotComputed | null,
    ev: MarketEvAvailable | MarketEvUnavailable | null = marketEv,
  ) {
    return renderWithProviders(
      <HorseEntriesTable entries={raceEntries} predictions={predictions}
        canonicalConsistent={true} marketEv={ev} attention={attention} now={NOW} />,
    );
  }

  function useRules() {
    server.use(
      http.get("*/api/v1/attention-rules", () => HttpResponse.json(attentionRulesFixture())),
    );
  }

  it("one chip 「注目条件 S1・研究中」 on the chip horse, nothing on the others (SC-002)", () => {
    const { container } = renderTable(
      race([chipHorse("2020000001", 1), attentionNoChipHorseFixture({ horse_id: "nk:99999" })]),
    );
    const cell = evCell(rowOf(container, "本登録馬"));
    const chips = mainChips(cell);
    expect(chips).toHaveLength(1);
    const chip = chips[0];
    expect(chip.textContent).toBe("注目条件 S1・研究中");
    expect(chip.getAttribute("role")).toBe("note");
    expect(chip.getAttribute("aria-label")).toBe("注目条件 S1・研究中");
    // levels {backtest 3, prospective 1, price noise 2} + freshness 2 → min = 1 (text only)
    expect(chip.className).toContain("attn-chip--1");
    // no threshold figure, no sub chip, no 「判断時点のみ該当」 while the current values match
    expect(within(cell).getByTestId("attention-chips").textContent).not.toMatch(/%|倍|超/);
    expect(cell.querySelector(".attn-chip--sub")).toBeNull();
    expect(cell.querySelector(".attn-now")).toBeNull();
    // the value column keeps the latest (ens15) market-ev with its 推定 badge
    expect(cell).toHaveTextContent("124.6%");
    assertPseudoLabelCoverage(container, ["124.6%"]);

    expect(mainChips(rowOf(container, "サロゲート馬"))).toHaveLength(0);
    expect(mainChips(container.querySelector("table")!)).toHaveLength(1);
  });

  it("adds the sub chip 「S2」 only when the API says chip_s2 (US1 scenario 2)", () => {
    const applicable = ["S1", "S2", "S3", "S4", "S5"] as AttentionHorse["applicable"];
    const { container } = renderTable(
      race([
        chipHorse("2020000001", 1, {
          applicable, chip_s2: true, stages: stagesFor(applicable),
          pick_status: pickStatusFor(applicable),
        }),
        chipHorse("nk:99999", 2),
      ]),
    );
    const s2 = evCell(rowOf(container, "本登録馬"));
    expect(mainChips(s2).map((c) => c.textContent)).toEqual(["注目条件 S1・研究中"]);
    const sub = s2.querySelectorAll(".attn-chip--sub");
    expect(sub).toHaveLength(1);
    expect(sub[0].textContent).toBe("S2");
    expect(evCell(rowOf(container, "サロゲート馬")).querySelector(".attn-chip--sub")).toBeNull();
  });

  it("main chip S2 (S1 failed) → 「注目条件 S2・観察中」 without a sub chip (US1 scenario 10)", () => {
    const applicable = ["S1", "S2", "S3", "S4", "S5"] as AttentionHorse["applicable"];
    const stages = { S1: stageDetail("failed", 300), S2: stageDetail("observing") };
    const { container } = renderTable(
      race([
        chipHorse("2020000001", 1, {
          applicable, chip_rule: "S2", chip_stage: stages.S2, chip_s2: false,
          stages: stagesFor(applicable, stages), pick_status: pickStatusFor(applicable),
        }),
      ]),
    );
    const cell = evCell(rowOf(container, "本登録馬"));
    expect(mainChips(cell).map((c) => c.textContent)).toEqual(["注目条件 S2・観察中"]);
    expect(cell.querySelector(".attn-chip--sub")).toBeNull();
  });

  it("a horse whose chip rule failed keeps the chip, stage name as the label, level 1", () => {
    const applicable = ["S4"] as AttentionHorse["applicable"];
    const { container } = renderTable(
      race([
        chipHorse("2020000001", 1, {
          applicable, chip_rule: "S4", chip_stage: stageDetail("failed", 300),
          stages: stagesFor(applicable, { S4: stageDetail("failed", 300) }),
          pick_status: pickStatusFor(applicable),
          // every other axis at 3 and the current row 5 min old: only the closed stage pins it
          levels: { backtest: 3, prospective: 3, price_noise: 3 },
          current: evSnapshotFixture({ odds_observed_at: iso(NOW - 5 * MIN) }),
        }),
      ]),
    );
    const row = rowOf(container, "本登録馬");
    const chips = mainChips(evCell(row));
    expect(chips.map((c) => c.textContent)).toEqual(["注目条件 S4・300 点不通過"]);
    expect(chips[0].className).toContain("attn-chip--1");
    expect(row.className).not.toContain("entry--ev-over");
  });

  it.each([
    ["no_longer", "判断時点のみ該当", evSnapshotFixture({ odds: 45.0,
      odds_observed_at: iso(NOW - 5 * MIN) })],
    ["unknown", "現在値なし", null],
  ] as const)("chip_now=%s → 「%s」 and the weakest emphasis (US1 scenarios 8/11)", (chipNow, text, current) => {
    const { container } = renderTable(
      race([
        chipHorse("2020000001", 1, {
          chip_now: chipNow,
          current,
          levels: { backtest: 3, prospective: 3, price_noise: 3 },
        }),
      ]),
    );
    const row = rowOf(container, "本登録馬");
    const cell = evCell(row);
    const chips = mainChips(cell);
    expect(chips.map((c) => c.textContent)).toEqual(["注目条件 S1・研究中"]);
    expect(chips[0].className).toContain("attn-chip--1");
    expect(cell.querySelector(".attn-now")?.textContent).toBe(text);
    expect(row.className).not.toContain("entry--ev-over");
  });

  it("emphasis level 3 (synthetic: all axes 3, 5 min fresh) → filled chip + the white-frame row only there", () => {
    const top = { backtest: 3, prospective: 3, price_noise: 3 } as const;
    const { container } = renderTable(
      race([
        chipHorse("2020000001", 1, {
          levels: top, current: evSnapshotFixture({ odds_observed_at: iso(NOW - 5 * MIN) }),
        }),
        // same axes but the latest odds are 42 min old → freshness 2 → outline only, no frame
        chipHorse("nk:99999", 2, { levels: top }),
      ]),
    );
    const framed = rowOf(container, "本登録馬");
    expect(framed.className).toContain("entry--ev-over");
    expect(mainChips(evCell(framed))[0].className).toContain("attn-chip--3");

    const outlined = rowOf(container, "サロゲート馬");
    expect(outlined.className).not.toContain("entry--ev-over");
    expect(mainChips(evCell(outlined))[0].className).toContain("attn-chip--2");
    expect(container.querySelectorAll("tr.entry--ev-over")).toHaveLength(1);
  });

  it("S5 only → no chip; the breakdown says 「対照 S5 に該当」 (US1 scenario 7)", async () => {
    const applicable = ["S5"] as AttentionHorse["applicable"];
    const { container } = renderTable(
      race([
        chipHorse("2020000001", 1, {
          applicable, chip_rule: null, chip_stage: null, chip_now: null, levels: null,
          stages: stagesFor(applicable), pick_status: pickStatusFor(applicable),
        }),
      ]),
    );
    const row = rowOf(container, "本登録馬");
    expect(row.querySelector(".attn-chip")).toBeNull();
    expect(row.className).not.toContain("entry--ev-over");
    await userEvent.click(within(row).getByRole("button", { name: "注目条件の内訳" }));
    expect(screen.getByTestId("attention-panel").textContent).toBe("対照 S5 に該当");
  });

  it("a cancelled horse shows no chip", () => {
    const { container } = renderTable(race([chipHorse("2020000004", 4)]));
    const row = rowOf(container, "取消馬");
    expect(row.querySelector(".attn-chip")).toBeNull();
    expect(row.className).not.toContain("entry--ev-over");
  });

  it("a cancelled horse's breakdown (pick not voided yet) shows the scratch, never the chip header", async () => {
    // scratched after its pick, before the next computation appends the void: the API still
    // returns the S1 chip — the race table's entry status wins (spec「取消馬: チップなし」).
    useRules();
    const { container } = renderTable(race([chipHorse("2020000004", 4)]));
    const row = rowOf(container, "取消馬");
    await userEvent.click(within(row).getByRole("button", { name: "注目条件の内訳" }));
    const panel = screen.getByTestId("attention-panel");
    await waitFor(() =>
      expect(within(panel).getByTestId("attention-cancelled").textContent).toBe(
        "出走取消(判断時点の該当: S1, S3, S4, 対照 S5)",
      ),
    );
    expect(within(panel).queryByTestId("attention-panel-title")).toBeNull();
    expect(within(panel).queryByTestId("attention-badge-posthoc")).toBeNull();
    expect(within(panel).queryByTestId("attention-badge-unconfirmed")).toBeNull();
    expect(within(panel).queryByTestId("attention-progress")).toBeNull();
    expect(panel.querySelector(".attn-chip")).toBeNull();
    expect(panel.textContent).not.toMatch(/注目条件 S1・/);
  });

  it("adds no column: the same headers with and without the attention state", () => {
    const without = renderTable(null);
    const nWithout = without.container.querySelectorAll("thead th").length;
    without.unmount();
    const { container } = renderTable(race([chipHorse("2020000001", 1)]));
    expect(container.querySelectorAll("thead th")).toHaveLength(nWithout);
    container.querySelectorAll("tbody tr.entry-row").forEach((tr) =>
      expect(tr.querySelectorAll("td")).toHaveLength(nWithout),
    );
  });

  it("not computed → no chip and no breakdown toggle", () => {
    const { container } = renderTable(attentionNotComputed);
    expect(container.querySelector(".attn-chip")).toBeNull();
    expect(screen.queryByRole("button", { name: "注目条件の内訳" })).toBeNull();
    // without market-ev either, the 期待回収率 column stays hidden (137)
    const bare = renderTable(attentionNotComputed, null);
    expect(within(bare.container).queryByText("期待回収率")).toBeNull();
  });

  it("keeps the chip with 「現在値なし」 while market-ev is 再計算待ち (US1 scenario 11)", async () => {
    useRules();
    const { container } = renderTable(
      race([
        chipHorse("2020000001", 1, {
          chip_now: "unknown", current: null, field_changed_after_pick: true,
        }),
      ]),
      fieldChanged,
    );
    // the column shows (values "—") so the judgment-time chip is not lost
    expect(screen.getByText("期待回収率").closest("th")).not.toBeNull();
    const row = rowOf(container, "本登録馬");
    const cell = evCell(row);
    expect(cell.querySelector("[data-pseudo]")).toBeNull();
    expect(cell.textContent).toMatch(/^—/);
    expect(mainChips(cell).map((c) => c.textContent)).toEqual(["注目条件 S1・研究中"]);
    expect(cell.querySelector(".attn-now")?.textContent).toBe("現在値なし");
    await userEvent.click(within(row).getByRole("button", { name: "注目条件の内訳" }));
    expect(await screen.findByTestId("attention-field-changed")).toHaveTextContent(
      "出走馬がその後変わっています",
    );
  });

  it("the breakdown opens in the expansion row (内訳 toggle; 寄与 opens the same row)", async () => {
    useRules();
    const { container } = renderTable(race([chipHorse("2020000001", 1)]));
    const row = rowOf(container, "本登録馬");
    const toggle = within(row).getByRole("button", { name: "注目条件の内訳" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const panel = await screen.findByTestId("attention-panel");
    expect(await within(panel).findByText(/通算\(2010〜26\)/)).toBeInTheDocument();
    const expansion = container.querySelector<HTMLTableCellElement>("tr.explanation-row td");
    expect(expansion?.colSpan).toBe(container.querySelectorAll("thead th").length);
    // the score-contribution panel shares the row (predictions exist)
    expect(within(expansion!).getByText(/スコア寄与/)).toBeInTheDocument();

    // closing and reopening via 寄与 shows the same breakdown
    await userEvent.click(toggle);
    expect(screen.queryByTestId("attention-panel")).toBeNull();
    await userEvent.click(within(row).getByRole("button", { name: "スコア寄与" }));
    expect(screen.getByTestId("attention-panel")).toBeInTheDocument();
  });

  it("scoped display discipline + ROI basis labels over the chips and every opened panel (SC-007)", async () => {
    useRules();
    const applicable = ["S1", "S2", "S3", "S4", "S5"] as AttentionHorse["applicable"];
    const { container } = renderTable(
      race([
        chipHorse("2020000001", 1, {
          applicable, chip_s2: true, stages: stagesFor(applicable),
          pick_status: pickStatusFor(applicable),
        }),
        chipHorse("nk:99999", 2, { chip_now: "no_longer" }),
        chipHorse("2020000003", 3, {
          applicable: ["S4"], chip_rule: "S4", chip_stage: stageDetail("failed", 300),
          stages: stagesFor(["S4"], { S4: stageDetail("failed", 300) }),
          pick_status: pickStatusFor(["S4"]), chip_now: "unknown", current: null,
        }),
      ]),
    );
    // expand every breakdown first (a closed panel would make the checks vacuous)
    for (const btn of screen.getAllByRole("button", { name: "注目条件の内訳" })) {
      await userEvent.click(btn);
    }
    const panels = await screen.findAllByTestId("attention-panel");
    expect(panels).toHaveLength(3);
    await screen.findAllByText(/通算\(2010〜26\)/);
    const chipGroups = Array.from(container.querySelectorAll('[data-testid="attention-chips"]'));
    expect(chipGroups).toHaveLength(3);
    assertAttentionDiscipline([...chipGroups, ...panels]);
    for (const p of panels) {
      expect(assertRoiBasisCoverage(p)).toBeGreaterThanOrEqual(8);
      assertRoiBasisLabels(p); // no percentage outside a labelled node
    }
    // the toggles are inside the attention scope too: their labels stay neutral
    for (const btn of screen.getAllByRole("button", { name: "注目条件の内訳" })) {
      expect(btn.getAttribute("aria-label")).not.toMatch(/印|推奨|おすすめ/);
    }
  });
});
