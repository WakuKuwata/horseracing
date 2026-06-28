import { useMemo, useState } from "react";

import type { HorseEntry, HorsePrediction } from "../api/types";
import { formatNum, formatPct, PLACEHOLDER } from "../lib/format";

type Pred = Pick<HorsePrediction, "win" | "top2" | "top3" | "market_win_prob">;

type ColKey =
  | "frame"
  | "horse_number"
  | "horse_name"
  | "jockey_name"
  | "weight"
  | "odds"
  | "win"
  | "market_win_prob";

type Row = HorseEntry & { pred?: Pred };

const NUMERIC: ColKey[] = ["horse_number", "weight", "odds", "win", "market_win_prob"];

const COLUMNS: { key: ColKey; label: string }[] = [
  { key: "horse_number", label: "枠/馬番" },
  { key: "horse_name", label: "馬名" },
  { key: "jockey_name", label: "騎手" },
  { key: "weight", label: "馬体重" },
  { key: "odds", label: "単勝" },
  { key: "win", label: "勝率(p)" },
  { key: "market_win_prob", label: "市場(q)" },
];

function value(row: Row, key: ColKey): number | string | null | undefined {
  if (key === "win" || key === "market_win_prob") return row.pred?.[key] ?? null;
  return (row as Record<string, unknown>)[key] as number | string | null | undefined;
}

/** Sortable per-horse entry table: 枠(色) 馬番 馬名(性齢) 騎手(斤量) 馬体重 単勝(人気) 勝率p 市場q.
 *  Names instead of ids; click a header to sort (toggles asc/desc). Cancelled horses are dimmed. */
export function HorseEntriesTable({
  entries,
  predictions,
}: {
  entries: HorseEntry[];
  predictions: HorsePrediction[];
}) {
  const [sortKey, setSortKey] = useState<ColKey>("horse_number");
  const [asc, setAsc] = useState(true);

  const rows: Row[] = useMemo(() => {
    const byId = new Map(predictions.map((p) => [p.horse_id, p]));
    return entries.map((e) => ({ ...e, pred: byId.get(e.horse_id) }));
  }, [entries, predictions]);

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

  return (
    <table className="entries-table">
      <thead>
        <tr>
          {COLUMNS.map((c) => (
            <th
              key={c.key}
              className={`sortable ${NUMERIC.includes(c.key) ? "num" : ""}`}
              aria-sort={sortKey === c.key ? (asc ? "ascending" : "descending") : "none"}
              onClick={() => toggle(c.key)}
            >
              {c.label}
              {sortKey === c.key ? (asc ? " ▲" : " ▼") : ""}
            </th>
          ))}
          <th>状態</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => {
          const cancelled = r.entry_status !== "started";
          return (
            <tr key={r.horse_id} className={cancelled ? "entry--cancelled" : ""}>
              <td>
                <span className="umaban-cell">
                  <span className={`waku waku-${r.frame ?? 0}`}>{r.frame ?? PLACEHOLDER}</span>
                  <span className="umaban-no">{r.horse_number ?? PLACEHOLDER}</span>
                </span>
              </td>
              <td>
                <span className="cell-main">{r.horse_name ?? PLACEHOLDER}</span>
                <span className="cell-sub">{(r.sex ?? "") + (r.age != null ? r.age : "")}</span>
              </td>
              <td>
                <span className="cell-main">{r.jockey_name ?? PLACEHOLDER}</span>
                <span className="cell-sub">
                  {r.jockey_weight != null ? `${formatNum(r.jockey_weight, 1)}kg` : ""}
                </span>
              </td>
              <td className="num">
                <span className="cell-main">{r.weight != null ? r.weight : PLACEHOLDER}</span>
                <span className="cell-sub">
                  {r.weight_diff != null ? `(${r.weight_diff > 0 ? "+" : ""}${r.weight_diff})` : ""}
                </span>
              </td>
              <td className="num">
                <span
                  className={
                    "cell-main" + (r.odds != null && r.odds < 10 ? " odds-low" : "")
                  }
                >
                  {formatNum(r.odds, 1)}
                </span>
                <span className="cell-sub">
                  {r.popularity != null ? `${r.popularity}人気` : ""}
                </span>
              </td>
              <td className="num">{formatPct(r.pred?.win)}</td>
              <td className="num">{formatPct(r.pred?.market_win_prob)}</td>
              <td>{cancelled ? r.entry_status : ""}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
