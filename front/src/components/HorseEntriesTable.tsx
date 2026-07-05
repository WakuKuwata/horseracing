import { Fragment, useMemo, useState } from "react";

import { Link } from "react-router-dom";

import type { HorseEntry, HorsePrediction } from "../api/types";
import { formatNum, formatPct, PLACEHOLDER } from "../lib/format";
import { DataBackingBadge } from "./DataBackingBadge";
import { ExplanationPanel } from "./ExplanationPanel";

// Feature 029: link a name to its profile when an id is present. `nk:` surrogates DO resolve to a
// profile (the surrogate horse/jockey exists in the DB with its scraped identity + accumulated
// results), so they are linkable too; only a null/empty id has no target → render plain text.
function isLinkable(id: string | null | undefined): id is string {
  return !!id;
}

type Pred = Pick<
  HorsePrediction,
  "win" | "top2" | "top3" | "market_win_prob" | "explanation" | "divergence" | "prior_starts_band"
>;

type ColKey =
  | "frame"
  | "horse_number"
  | "horse_name"
  | "jockey_name"
  | "weight"
  | "odds"
  | "win";

type Row = HorseEntry & { pred?: Pred };

const NUMERIC: ColKey[] = ["horse_number", "weight", "odds", "win"];

// 市場評価 (q = odds converted to a win share) lives INSIDE the 単勝 cell as a sub-line — it is
// the same information as the odds, so it shares the column (user decision 2026-07-02). Sorting
// by 単勝 therefore covers q too (q is monotone in the odds).
const BASE_COLUMNS: { key: ColKey; label: string; title?: string }[] = [
  { key: "horse_number", label: "枠/馬番" },
  { key: "horse_name", label: "馬名" },
  { key: "jockey_name", label: "騎手" },
  { key: "weight", label: "馬体重" },
  {
    key: "odds",
    label: "単勝",
    title:
      "単勝オッズと人気。下段の市場評価=オッズを勝率に換算した市場の支持率 (1/オッズ)÷Σ(1/オッズ)。推定値であり実測ではありません(FLバイアス含む)",
  },
];

// win p is sortable (a model-side ordering); the 市場との差 column is intentionally NOT sortable —
// ordering by the disagreement would be an edge sort (021/040 US3 prohibition).
// top2/top3 are CUMULATIVE P(2着以内)/P(3着以内) — labelled 連対/複勝, stacked as sub-lines under
// the 勝率 (user decision: vertical stack, no extra columns). Sorting stays on win only.
const PRED_COLUMNS: { key: ColKey; label: string; title?: string }[] = [
  {
    key: "win",
    label: "モデル勝率",
    title: "上段=勝率、下段=連対率(2着以内)・複勝率(3着以内)。いずれもモデルの確率です",
  },
];

// 040 US3 divergence band (pre-registered p ⋛ q±max(0.03,0.5q)) — shown as a NEUTRAL colour on the
// 市場との差 value instead of a separate badge column. Two categorical hues (blue/purple), never
// win/loss green/red; "similar" stays muted. Full factual sentence lives in the tooltip.
type Div = NonNullable<HorsePrediction["divergence"]>;

const DIVERGENCE_LONG: Record<Div, string> = {
  market_higher: "市場評価がモデルより高い",
  model_higher: "モデル評価が市場より高い",
  similar: "モデルと市場の評価はほぼ同等",
};

const DIVERGENCE_TOOLTIP =
  "モデル勝率と市場評価の差です。意見の相違であり、的中や利益を保証するものではありません";

function value(row: Row, key: ColKey): number | string | null | undefined {
  if (key === "win") return row.pred?.win ?? null;
  return (row as Record<string, unknown>)[key] as number | string | null | undefined;
}

/** Thin horizontal bar under a probability figure — same scale for p and q (shared max) so the
 *  two columns are visually comparable. Neutral colours; NOT a signal. */
function ProbBar({
  value,
  max,
  variant,
}: {
  value: number | null | undefined;
  max: number;
  variant: "p" | "q";
}) {
  if (value == null || max <= 0) return null;
  const frac = Math.max(0, Math.min(1, value / max));
  return (
    <span
      className={`prob-bar prob-bar--${variant}`}
      style={{ width: `calc((100% - 0.8rem) * ${frac.toFixed(3)})` }}
      aria-hidden="true"
    />
  );
}

/** Sortable per-horse entry table: 枠(色) 馬番 馬名(性齢+出走歴) 騎手(斤量) 馬体重
 *  単勝(人気+市場評価) モデル勝率(連対率/複勝率を縦積み) 市場との差(乖離バンド色) 寄与.
 *  Prediction columns only render when a prediction run exists. 市場評価 (q) is pseudo (odds
 *  vote-share): its disclosure lives in the 単勝 header tooltip + the always-visible note under
 *  the table (user decision 2026-07-02 — badges were noise; the labelled sub-line + note keep
 *  021's "q never unlabelled" intent). Cancelled horses are dimmed with a badge next to the name
 *  (no dedicated 状態 column). */
export function HorseEntriesTable({
  entries,
  predictions,
  oddsAsOf,
  canonicalConsistent,
}: {
  entries: HorseEntry[];
  predictions: HorsePrediction[];
  oddsAsOf?: string | null;
  canonicalConsistent?: boolean | null;
}) {
  // Default sort = モデル勝率 desc (the prediction IS what this screen is for; user decision
  // 2026-07-02). Without predictions every win is null → the null-last comparator keeps the
  // API (馬番) order, so the no-prediction table still reads naturally.
  const [sortKey, setSortKey] = useState<ColKey>("win");
  const [asc, setAsc] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const hasPreds = predictions.length > 0;
  // 差(p−q) is only meaningful when the API confirms p and q share one canonical field (021 R1).
  const comparable = hasPreds && canonicalConsistent === true;

  function toggleExpand(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const rows: Row[] = useMemo(() => {
    const byId = new Map(predictions.map((p) => [p.horse_id, p]));
    return entries.map((e) => ({ ...e, pred: byId.get(e.horse_id) }));
  }, [entries, predictions]);

  // Shared bar scale: max over BOTH p and q so the two columns compare visually.
  const probMax = useMemo(
    () =>
      rows.reduce(
        (m, r) => Math.max(m, r.pred?.win ?? 0, r.pred?.market_win_prob ?? 0),
        0,
      ),
    [rows],
  );

  const sorted = useMemo(() => {
    const dir = asc ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = value(a, sortKey);
      const bv = value(b, sortKey);
      if (av == null && bv == null) return 0;
      if (av == null) return 1; // nulls last regardless of direction
      if (bv == null) return -1;
      if (NUMERIC.includes(sortKey)) return ((av as number) - (bv as number)) * dir;
      return String(av).localeCompare(String(bv), "ja") * dir;
    });
  }, [rows, sortKey, asc]);

  function toggle(key: ColKey) {
    if (key === sortKey) setAsc((v) => !v);
    else {
      setSortKey(key);
      setAsc(true);
    }
  }

  const sortableColumns = hasPreds ? [...BASE_COLUMNS, ...PRED_COLUMNS] : BASE_COLUMNS;
  // total columns for the expansion row colSpan
  const totalCols =
    sortableColumns.length + (comparable ? 1 : 0) + (hasPreds ? 1 : 0);

  return (
    <div className="table-scroll">
      <table className="entries-table">
        <thead>
          <tr>
            {sortableColumns.map((c) => (
              <th
                key={c.key}
                className={`sortable ${NUMERIC.includes(c.key) ? "num" : ""}`}
                aria-sort={sortKey === c.key ? (asc ? "ascending" : "descending") : "none"}
                title={c.title}
                onClick={() => toggle(c.key)}
              >
                {c.label}
                {sortKey === c.key ? (asc ? " ▲" : " ▼") : ""}
              </th>
            ))}
            {comparable && (
              <th
                className="num"
                title="モデル勝率 − 市場評価(pt)。正=モデルの方が高い。意見の相違であり推奨ではありません"
              >
                市場との差
              </th>
            )}
            {hasPreds && <th className="expand-head">寄与</th>}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => {
            const cancelled = r.entry_status !== "started";
            const isOpen = expanded.has(r.horse_id);
            const p = r.pred?.win;
            const q = r.pred?.market_win_prob;
            const diff = comparable && p != null && q != null ? p - q : null;
            const div = r.pred?.divergence ?? null;
            return (
              <Fragment key={r.horse_id}>
                <tr
                  className={`entry-row${i % 2 ? " row--alt" : ""}${
                    cancelled ? " entry--cancelled" : ""
                  }`}
                >
                  <td>
                    <span className="umaban-cell">
                      <span className={`waku waku-${r.frame ?? 0}`}>{r.frame ?? PLACEHOLDER}</span>
                      <span className="umaban-no">{r.horse_number ?? PLACEHOLDER}</span>
                    </span>
                  </td>
                  <td>
                    <span className="cell-main">
                      {isLinkable(r.horse_id) ? (
                        <Link to={`/horses/${r.horse_id}`}>{r.horse_name ?? r.horse_id}</Link>
                      ) : (
                        (r.horse_name ?? PLACEHOLDER)
                      )}
                      {cancelled && (
                        <span className="badge badge--cancelled">{r.entry_status}</span>
                      )}
                    </span>
                    <span className="cell-sub">
                      {(r.sex ?? "") + (r.age != null ? r.age : "")}
                      {hasPreds && r.pred?.prior_starts_band && (
                        <>
                          {" "}
                          <DataBackingBadge band={r.pred.prior_starts_band} />
                        </>
                      )}
                    </span>
                  </td>
                  <td>
                    <span className="cell-main">
                      {isLinkable(r.jockey_id) ? (
                        <Link to={`/jockeys/${r.jockey_id}`}>{r.jockey_name ?? r.jockey_id}</Link>
                      ) : (
                        (r.jockey_name ?? PLACEHOLDER)
                      )}
                    </span>
                    <span className="cell-sub">
                      {r.jockey_weight != null ? `${formatNum(r.jockey_weight, 1)}kg` : ""}
                    </span>
                  </td>
                  <td className="num">
                    <span className="cell-main">{r.weight != null ? r.weight : PLACEHOLDER}</span>
                    <span className="cell-sub">
                      {r.weight_diff != null
                        ? `(${r.weight_diff > 0 ? "+" : ""}${r.weight_diff})`
                        : ""}
                    </span>
                  </td>
                  <td className={`num${hasPreds ? " prob-cell" : ""}`}>
                    <span
                      className={
                        "cell-main" +
                        (r.popularity != null && r.popularity <= 3 ? " odds-low" : "")
                      }
                    >
                      {formatNum(r.odds, 1)}
                    </span>
                    <span className="cell-sub">
                      {r.popularity != null ? `${r.popularity}人気` : ""}
                    </span>
                    {/* q = this odds column converted to a win share — same column, labelled
                        sub-line (pseudo disclosure in the header tooltip + table note) */}
                    {hasPreds && q != null && (
                      <span className="cell-sub">市場評価 {formatPct(q)}</span>
                    )}
                    {hasPreds && <ProbBar value={q} max={probMax} variant="q" />}
                  </td>
                  {hasPreds && (
                    <td className="num prob-cell">
                      <span className="prob-text">{formatPct(p)}</span>
                      {r.pred?.top2 != null && (
                        <span className="cell-sub">連対 {formatPct(r.pred?.top2, 0)}</span>
                      )}
                      {r.pred?.top3 != null && (
                        <span className="cell-sub">複勝 {formatPct(r.pred?.top3, 0)}</span>
                      )}
                      <ProbBar value={p} max={probMax} variant="p" />
                    </td>
                  )}
                  {comparable && (
                    <td
                      className={`num diff-cell${div ? ` diff--${div}` : ""}`}
                      title={
                        div
                          ? `${DIVERGENCE_LONG[div]}。${DIVERGENCE_TOOLTIP}${
                              oddsAsOf ? `（市場評価の基準時点: ${oddsAsOf}）` : ""
                            }`
                          : undefined
                      }
                    >
                      {diff == null
                        ? PLACEHOLDER
                        : `${diff >= 0 ? "+" : ""}${(diff * 100).toFixed(1)}pt`}
                    </td>
                  )}
                  {hasPreds && (
                    <td className="expand-cell">
                      <button
                        type="button"
                        className="expand-btn"
                        aria-expanded={isOpen}
                        aria-label="スコア寄与"
                        title="スコア寄与（モデルの判断要因）を表示"
                        onClick={() => toggleExpand(r.horse_id)}
                      >
                        {isOpen ? "▾" : "▸"}
                      </button>
                    </td>
                  )}
                </tr>
                {isOpen && (
                  <tr className="explanation-row">
                    <td colSpan={totalCols}>
                      <ExplanationPanel explanation={r.pred?.explanation} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
