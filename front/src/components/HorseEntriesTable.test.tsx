import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HorseEntry, HorsePrediction } from "../api/types";
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
