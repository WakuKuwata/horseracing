import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ATTENTION_NOTE_TEXT } from "../components/AttentionNote";
import { server } from "../tests/server";
import { assertAttentionDiscipline, assertRoiBasisCoverage } from "../tests/attentionDiscipline";
import { assertRoiBasisLabels } from "../tests/attentionScope";
import {
  attentionAvailableFixture,
  happyHandlers,
  http,
  HttpResponse,
  marketEvAvailable,
  raceDetail,
  recommendationResponse,
} from "../tests/fixtures";
import { assertPseudoLabelCoverage } from "../tests/pseudo";
import { renderWithProviders } from "../tests/utils";
import { RaceDetailPage } from "./RaceDetailPage";

function renderDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="/races/:raceId" element={<RaceDetailPage />} />
    </Routes>,
    { route: "/races/200806010111" },
  );
}

describe("RaceDetailPage", () => {
  it("renders predictions and the prediction-run audit", async () => {
    server.use(...happyHandlers);
    renderDetail();
    // win probability rendered as percentage in the entries table.
    expect((await screen.findAllByText("32.0%")).length).toBeGreaterThan(0);
    // run audit surfaces which prediction_run was selected (constitution V)
    expect(screen.getByText("run-abc")).toBeInTheDocument();
    // model_version appears in the run audit and the calibration panel
    expect(screen.getAllByText("lgbm-006").length).toBeGreaterThan(0);
  });

  it("surfaces prediction API errors without hiding the race entries", async () => {
    server.use(...happyHandlers);
    server.use(
      http.get("*/api/v1/races/:id/predictions", () =>
        HttpResponse.json(
          { status: 503, code: "prediction_unavailable", detail: "prediction fetch failed" },
          { status: 503 },
        ),
      ),
    );
    renderDetail();

    expect(await screen.findByRole("alert")).toHaveTextContent("prediction_unavailable");
    expect(screen.getByText("prediction fetch failed")).toBeInTheDocument();
    expect(await screen.findByText("h1")).toBeInTheDocument();
  });

  // Feature 087 (T013A, codex C1): the betting-slip horse names/frames come from the RACE
  // DETAIL response — the prediction response has no horse_name/frame at all, so a slip that
  // renders names proves the wiring uses raceQuery, not predQuery.
  it("feeds the betting slip from the race-detail entries (names + frame colors)", async () => {
    server.use(
      http.get("*/api/v1/races/:id", () =>
        HttpResponse.json({
          ...raceDetail,
          horses: [
            { horse_id: "h1", horse_number: 1, entry_status: "active", horse_name: "サンプルホース", frame: 3 },
            { horse_id: "h2", horse_number: 2, entry_status: "active", horse_name: null, frame: null },
          ],
        }),
      ),
      http.get("*/api/v1/races/:id/recommendations", () =>
        HttpResponse.json({
          ...recommendationResponse,
          items: [
            { ...recommendationResponse.items[1], recommendation_id: "rec-w1", settled: false,
              hit: undefined, counterfactual_snapshot_gross_return: undefined,
              counterfactual_snapshot_net_return: undefined },
          ],
        }),
      ),
      // overrides FIRST — MSW resolves handlers first-match-wins
      ...happyHandlers,
    );
    renderDetail(); // the 買い目推奨 tab is the default tab

    const card = await screen.findByTestId("bet-slip-card-rec-w1");
    expect(within(card).getByText("サンプルホース")).toBeInTheDocument();
    expect(card.querySelector(".frame-chip--3")).toHaveTextContent("1");
  });

  // Feature 137: the page fetches the market-aware expected return on its own (no model_version),
  // puts the note right before the entries table and passes the value into the table column.
  // Feature 138 (FR-010): the 137 「120%超」 chip and the API-flagged row highlight are gone — the
  // emphasis is the 注目条件 chip now (attention state below), never `exceeds_threshold`.
  it("wires 期待回収率: note before the table, badged column, no 137 highlight", async () => {
    const marketEvUrls: string[] = [];
    server.use(
      http.get("*/api/v1/races/:id", () =>
        HttpResponse.json({
          ...raceDetail,
          horses: [
            { horse_id: "h1", horse_number: 1, entry_status: "started", horse_name: "イチ" },
            { horse_id: "h2", horse_number: 2, entry_status: "started", horse_name: "ニ" },
          ],
        }),
      ),
      http.get("*/api/v1/races/:id/market-ev", ({ request }) => {
        marketEvUrls.push(request.url);
        return HttpResponse.json(marketEvAvailable);
      }),
      // overrides FIRST — MSW resolves handlers first-match-wins
      ...happyHandlers,
    );
    const { container } = renderDetail();

    // distinct values (1.237 / 0.864) so no other test's percentage lookup collides
    expect(await screen.findByText("123.7%")).toBeInTheDocument();
    expect(screen.getByText("86.4%")).toBeInTheDocument();
    assertPseudoLabelCoverage(container, ["123.7%", "86.4%"]);

    const note = screen.getByTestId("expected-return-note");
    expect(note).toHaveTextContent("市場連動モデル(mev-binary-v2)");
    const table = container.querySelector("table.entries-table")!;
    // the note sits BEFORE the entries table
    expect(note.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // h1 is flagged by the API (exceeds_threshold) but 138 removed the 137 chip/highlight: no
    // 「120%超」, no framed row (the attention state is the default not_computed here).
    expect(container.querySelectorAll("tr.entry--ev-over")).toHaveLength(0);
    expect(screen.queryByText("120%超")).toBeNull();
    expect(container.querySelector(".attn-chip")).toBeNull();

    // independent of the win-model selection: no model_version (or any) query parameter
    expect(marketEvUrls.length).toBeGreaterThan(0);
    for (const u of marketEvUrls) expect(new URL(u).search).toBe("");
  });

  it("default (not computed): reason in the note, no 期待回収率 column", async () => {
    server.use(...happyHandlers);
    renderDetail();
    expect(
      await screen.findByText("このレースの期待回収率はまだ計算されていません"),
    ).toBeInTheDocument();
    expect(screen.queryByText("期待回収率", { selector: "th" })).toBeNull();
  });

  // Feature 138 (T039): the page reads /races/{id}/attention on its own (no query parameter),
  // passes it to the table (chips are judgment-time values from the API), shows the FR-011 note
  // right after the 期待回収率 note and the rules list (closed) right after the table hint.
  it("wires 注目条件: chip from the API, note after the 期待回収率 note, rules list after the hint", async () => {
    const attentionUrls: string[] = [];
    server.use(
      // started entries (a non-started entry is a cancelled row, which never gets a chip)
      http.get("*/api/v1/races/:id", () =>
        HttpResponse.json({
          ...raceDetail,
          horses: [
            { horse_id: "h1", horse_number: 1, entry_status: "started", horse_name: "イチ" },
            { horse_id: "h2", horse_number: 2, entry_status: "started", horse_name: "ニ" },
          ],
        }),
      ),
      http.get("*/api/v1/races/:id/attention", ({ request }) => {
        attentionUrls.push(request.url);
        return HttpResponse.json(attentionAvailableFixture());
      }),
      // overrides FIRST — MSW resolves handlers first-match-wins
      ...happyHandlers,
    );
    const { container } = renderDetail();

    // h1 (S1 chip horse) gets exactly one chip; h2 (no applicable rule) none
    const chip = await screen.findByRole("note", { name: "注目条件 S1・研究中" });
    expect(chip).toHaveTextContent("注目条件 S1・研究中");
    expect(container.querySelectorAll(".attn-chip")).toHaveLength(1);
    const rows = Array.from(container.querySelectorAll<HTMLTableRowElement>("tr.entry-row"));
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("イチ")).toBeInTheDocument();
    expect(rows[0].contains(chip)).toBe(true);
    expect(rows[1].querySelector(".attn-chip")).toBeNull();
    // the API says this race has results → freshness 発走後 → level 1; never the 137 frame
    expect(chip).toHaveClass("attn-chip--1");
    expect(container.querySelectorAll("tr.entry--ev-over")).toHaveLength(0);
    // the response landed: neither the failure nor the loading line of the note remains
    expect(screen.queryByTestId("attention-error")).toBeNull();
    expect(screen.queryByTestId("attention-loading")).toBeNull();

    // independent of the win-model selection: no query parameter at all
    expect(attentionUrls.length).toBeGreaterThan(0);
    for (const u of attentionUrls) expect(new URL(u).search).toBe("");

    // FR-011 note: verbatim, after the 期待回収率 note and before the entries table
    const evNote = screen.getByTestId("expected-return-note");
    const note = screen.getByTestId("attention-note");
    expect(note).toHaveTextContent(ATTENTION_NOTE_TEXT);
    const table = container.querySelector("table.entries-table")!;
    expect(evNote.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(note.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // rules list: right after the table hint, closed by default
    const rules = screen.getByTestId("attention-rules-panel") as HTMLDetailsElement;
    expect(rules.previousElementSibling).toHaveClass("table-hint");
    expect(table.compareDocumentPosition(rules) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(rules.open).toBe(false);
    expect(rules.previousElementSibling).toHaveTextContent("注目条件の内訳");

    // open everything the page can fold (087 lesson), then check the attention scope only
    await userEvent.click(within(rules).getByText(/注目条件の一覧/, { selector: "summary" }));
    expect(rules.open).toBe(true);
    await within(rules).findByTestId("attention-rule-S1");
    await userEvent.click(within(rows[0]).getByRole("button", { name: "注目条件の内訳" }));
    const panel = await screen.findByTestId("attention-panel");
    await within(panel).findByText(/通算\(2010〜26\)/);

    const chipGroups = Array.from(container.querySelectorAll('[data-testid="attention-chips"]'));
    assertAttentionDiscipline([...chipGroups, note, rules, panel]);
    expect(assertRoiBasisCoverage(panel)).toBeGreaterThan(0);
    assertRoiBasisLabels(panel);
    assertRoiBasisLabels(rules);
  });

  it("attention not computed: no chip, but the note and the rules list stay", async () => {
    server.use(...happyHandlers);
    const { container } = renderDetail();
    expect(await screen.findByTestId("attention-rules-panel")).toBeInTheDocument();
    expect(screen.getByTestId("attention-note")).toHaveTextContent(ATTENTION_NOTE_TEXT);
    expect(container.querySelector(".attn-chip")).toBeNull();
    // no 期待回収率 column either (both states are not_computed) and no 内訳 hint
    expect(screen.queryByText("期待回収率", { selector: "th" })).toBeNull();
    expect(container.querySelector("p.table-hint")).not.toHaveTextContent("注目条件の内訳");
  });

  it("attention fetch error: neutral (no alert), the race entries stay", async () => {
    server.use(
      http.get("*/api/v1/races/:id/attention", () =>
        HttpResponse.json(
          { status: 503, code: "attention_unavailable", detail: "down" },
          { status: 503 },
        ),
      ),
      ...happyHandlers,
    );
    const { container } = renderDetail();
    expect(await screen.findByText("h1")).toBeInTheDocument();
    // the failure is SAID (a table without chips alone reads exactly like "no horse matches"),
    // neutrally: inside the note, no alert role, no error colour
    const err = await screen.findByTestId("attention-error");
    expect(err.textContent).toBe("注目条件を取得できませんでした(HTTP 503 attention_unavailable)");
    expect(screen.getByTestId("attention-note")).toContainElement(err);
    expect(screen.queryByTestId("attention-loading")).toBeNull();
    expect(container.querySelector(".attn-chip")).toBeNull();
    expect(container.querySelector(".attn-toggle")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(err.closest(".state--error")).toBeNull();
  });
});
