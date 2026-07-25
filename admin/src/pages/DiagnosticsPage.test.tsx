import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { http, HttpResponse } from "../tests/fixtures";
import { server } from "../tests/server";
import { renderWithProviders } from "../tests/utils";
import { DiagnosticsPage } from "./DiagnosticsPage";

const BASE = "*/api/v1";

const response = {
  kind: "segment_edge",
  computed_at: "2026-07-03T10:00:00Z",
  date_from: "2021-01-01",
  date_to: "2025-10-26",
  logic_version: "diag=segment_edge;axes=047-preregistered;v=diag-0.1.0",
  n_horses: 181341,
  note: "SECONDARY diagnostic (047). Not a buy signal.",
  rows: [
    { axis: "q_band", segment: "0.05-0.15", n: 50000, win_rate: 0.06,
      logloss_p: 0.24, logloss_q: 0.2275, gap: 0.0125, mean_p: 0.07, mean_q: 0.08 },
    { axis: "q_band", segment: "q>=0.30(本命)", n: 12000, win_rate: 0.413,
      logloss_p: 0.65, logloss_q: 0.419, gap: 0.2306, mean_p: 0.185, mean_q: 0.405 },
    { axis: "surface", segment: "芝", n: 90000, win_rate: 0.08,
      logloss_p: 0.234, logloss_q: 0.202, gap: 0.032, mean_p: 0.08, mean_q: 0.085 },
  ],
};

describe("DiagnosticsPage", () => {
  it("renders per-axis tables in PERSISTED order with freshness + disclaimer", async () => {
    server.use(http.get(`${BASE}/diagnostics/segment-edge`, () => HttpResponse.json(response)));
    const { container } = renderWithProviders(<DiagnosticsPage />);
    await screen.findByText("q_band");
    expect(screen.getByText("surface")).toBeInTheDocument();
    // disclaimer + freshness always on screen
    expect(container.textContent).toContain("SECONDARY");
    expect(container.textContent).toContain("2021-01-01");
    expect(screen.getByText("181,341")).toBeInTheDocument();
    // rows stay in persisted (pre-registered) order — NOT sorted by gap: within q_band,
    // 0.05-0.15 (gap +0.0125) comes BEFORE q>=0.30 (gap +0.2306)
    const cells = [...container.querySelectorAll("td:first-child")].map((c) => c.textContent);
    expect(cells.indexOf("0.05-0.15")).toBeLessThan(cells.indexOf("q>=0.30(本命)"));
    expect(container.textContent).not.toContain("NaN");
  });

  it("shows the CLI instruction when nothing is persisted (typed 404)", async () => {
    server.use(http.get(`${BASE}/diagnostics/segment-edge`, () =>
      HttpResponse.json(
        { status: 404, code: "diagnostic_unavailable", detail: "none" }, { status: 404 })));
    const { container } = renderWithProviders(<DiagnosticsPage />);
    await screen.findByText(/永続化された診断がまだありません/);
    expect(container.textContent).toContain("segment-diagnostic");
    expect(container.querySelector('[data-code="diagnostic_unavailable"]')).not.toBeNull();
  });
});

// --- Feature 083: segment-accuracy section (082 instrument viewer, typed v1) --------------------

const saCI = { point: -0.5, ci_low: -0.55, ci_high: -0.45, n_days: 100, no_decision: false,
               ci_note: "pointwise 95% CI, NOT adjusted for multiple comparisons" };
const saBins = [{ lo: 0.0, hi: 0.1, n: 10, pred_mean: 0.05, realized: 0.06,
                  wilson_low: 0.01, wilson_high: 0.2 }];

const saResponse = {
  kind: "segment_accuracy",
  diagnostic_run_id: "a9336986-ddb7-4d95-8278-8b87af2823c7",
  computed_at: "2026-07-25T10:00:00Z",
  date_from: "2019-01-01",
  date_to: "2026-07-12",
  logic_version: "segment-accuracy;model=lgbm-064-f02acc;mask=sa-mask-v1;metric=sa-v1",
  payload: {
    instrument_contract: {
      kind: "segment_accuracy", secondary: true, can_adopt: false,
      estimand: "active-recipe historical OOF accuracy",
      discovery_rule: "new pre-registration with discovery_run_id required",
      ci_note: "all CIs are pointwise and NOT adjusted for multiple comparisons",
      known_confounds: ["model-age-within-year"],
      metric_contract_version: "sa-v1",
      mask_library_version: "sa-mask-v1", mask_library_hash: "mlh0123456789ab",
    },
    provenance: {
      base_model_version: "lgbm-064-f02acc", feature_version: "features-018",
      feature_hash: "fh", attestation_digest: "ad0123456789abcd",
      bundle_digest: "c3edef42f698abcd", prediction_checksum: "pc",
      oof_race_set_hash: "orsh", scored_race_set_hash: "srsh", label_snapshot_hash: "lsh",
      train_floor: "full-history", eval_window: ["2019-01-01", "2026-07-12"],
      first_valid_year: 2019, fold_boundaries: [2019, 2020],
      probability_stage: "model-internal calibrated win prob (pre-two-gamma)",
      code_sha: "sha0123456789", seed: 20260725, bootstrap_b: 2000,
      metric_contract_version: "sa-v1",
      mask_library_version: "sa-mask-v1", mask_library_hash: "mlh0123456789ab",
    },
    population: { n_scored_races: 26006, n_scored_horses: 357467,
                  exclusions: { ineligible_winner_count: 43, no_finished_label: 12 } },
    axes: [
      { axis_id: "year", family: "temporal", grain: "race", origin: "core",
        definition: { buckets: "eval calendar year" }, mask_definition_hash: "h-year",
        buckets: { "2024": {
          grain: { winner_nll: "race", calibration: "started_horse_within_selected_races" },
          n_races: 3448, n_horses: 47000,
          excess_nll_uniform: { ...saCI, point: -0.5646 },
          winner_nll: 1.99, uniform_nll: 2.55,
          market: { n_market_complete_races: 3448, n_total_races: 3448,
                    market_nll: 1.86, winner_nll_market_subset: 1.99,
                    excess_nll_market: 0.1368 },
          by_year: { "2024": { n_races: 3448, excess_nll_uniform_point: -0.5646 } },
          calibration: { grain_note: "note", bins: saBins, ece: 0.0012,
                         calibration_in_the_large: null,
                         citl_note: "structurally 0 at race grain", n: 47000 },
          ece_ci: { ...saCI, point: 0.0012 } } } },
      { axis_id: "rotation_band", family: "post_081_exploratory", grain: "horse",
        origin: "post_081_exploratory", definition: { edges_days: [8, 15, 29, 71] },
        mask_definition_hash: "h-rot",
        // JSONB does NOT keep key order — fixture deliberately inserts in REVERSED codepoint
        // order; the UI must still render codepoint-ascending (fixed, value-independent).
        buckets: {
          "missing": {
            grain: { excess_logloss: "horse", winner_nll: "NOT_AVAILABLE_AT_HORSE_GRAIN" },
            n_horses: 35832, n_races: 9000,
            excess_logloss_vs_uniform: { ...saCI, point: -0.0254 },
            by_year: { "2024": { n_horses: 35832, excess_logloss_point: -0.0254 } },
            calibration: { grain_note: "note", bins: saBins, ece: 0.0022,
                           calibration_in_the_large: -0.00115, n: 35832 },
            ece_ci: { ...saCI, point: 0.0022 } },
          ">70": {
            grain: { excess_logloss: "horse", winner_nll: "NOT_AVAILABLE_AT_HORSE_GRAIN" },
            n_horses: 82289, n_races: 4756,
            excess_logloss_vs_uniform: { ...saCI, point: -0.0384 },
            by_year: { "2024": { n_horses: 82289, excess_logloss_point: -0.0384 } },
            calibration: { grain_note: "note", bins: saBins, ece: 0.0069,
                           calibration_in_the_large: -0.00683, n: 82289 },
            ece_ci: { ...saCI, point: 0.0069 } },
        } },
    ],
  },
};

const edge404 = () =>
  server.use(http.get(`${BASE}/diagnostics/segment-edge`, () =>
    HttpResponse.json(
      { status: 404, code: "diagnostic_unavailable", detail: "none" }, { status: 404 })));
const edgeOk = () =>
  server.use(http.get(`${BASE}/diagnostics/segment-edge`, () => HttpResponse.json(response)));
const saOk = () =>
  server.use(http.get(`${BASE}/diagnostics/segment-accuracy`, () =>
    HttpResponse.json(saResponse)));

describe("DiagnosticsPage — segment accuracy section (083)", () => {
  it("renders axes in payload order, buckets in FIXED codepoint order, with contract notes", async () => {
    edgeOk(); saOk();
    const { container } = renderWithProviders(<DiagnosticsPage />);
    await screen.findByText("year");
    // axes: payload (frozen library) order — year before rotation_band
    const summaries = [...container.querySelectorAll(".sa-axis summary")].map(
      (s) => s.textContent ?? "");
    expect(summaries.findIndex((t) => t.includes("year"))).toBeLessThan(
      summaries.findIndex((t) => t.includes("rotation_band")));
    // buckets: codepoint order (">70" < "missing") even though the fixture inserted reversed
    const rotTable = [...container.querySelectorAll(".sa-axis")].find(
      (d) => d.querySelector("summary")?.textContent?.includes("rotation_band"));
    const bucketCells = [...(rotTable?.querySelectorAll("tbody td:first-child") ?? [])].map(
      (c) => c.textContent);
    expect(bucketCells).toEqual([">70", "missing"]);
    // horse grain shows n_HORSES (not n_races)
    expect(container.textContent).toContain("82,289");
    // anti-fishing + provenance surfaced
    expect(container.textContent).toContain("多重比較未調整");
    expect(container.textContent).toContain("081由来(独立確認には使えない)");
    expect(container.textContent).toContain("lgbm-064-f02acc");
    expect(container.textContent).toContain("a9336986-ddb7-4d95-8278-8b87af2823c7");
    expect(container.textContent).toContain("pre-two-gamma");
    // market column carries the same-population n breakdown
    expect(container.textContent).toContain("(n=3,448/3,448)");
    // no sorting controls, no NaN
    expect(container.querySelector(".sa-section button")).toBeNull();
    expect(container.querySelector(".sa-section select")).toBeNull();
    expect(container.textContent).not.toContain("NaN");
  });

  it("sections are INDEPENDENT: edge 404 does not hide the accuracy section", async () => {
    edge404(); saOk();
    const { container } = renderWithProviders(<DiagnosticsPage />);
    await screen.findByText("year");                                    // accuracy rendered
    expect(container.textContent).toContain("segment-diagnostic");      // edge empty state too
  });

  it("sections are INDEPENDENT: accuracy 404 does not hide the edge section", async () => {
    edgeOk();
    server.use(http.get(`${BASE}/diagnostics/segment-accuracy`, () =>
      HttpResponse.json(
        { status: 404, code: "diagnostic_unavailable", detail: "none" }, { status: 404 })));
    const { container } = renderWithProviders(<DiagnosticsPage />);
    await screen.findByText("q_band");                                  // edge rendered
    await screen.findByText(/accuracy-readout/);                        // accuracy empty state
    expect(container.querySelector('[data-code="diagnostic_unavailable"]')).not.toBeNull();
  });

  it("shows the fail-closed state on contract mismatch (typed 409)", async () => {
    edgeOk();
    server.use(http.get(`${BASE}/diagnostics/segment-accuracy`, () =>
      HttpResponse.json(
        { status: 409, code: "diagnostic_contract_unsupported", detail: "sa-v99" },
        { status: 409 })));
    const { container } = renderWithProviders(<DiagnosticsPage />);
    await screen.findByText(/fail-closed/);
    expect(container.querySelector('[data-code="diagnostic_contract_unsupported"]')).not.toBeNull();
  });
});
