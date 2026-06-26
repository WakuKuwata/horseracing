import { useState } from "react";

import { usePredictions } from "../api/queries";
import { EXOTIC_BET_TYPES } from "../lib/betTypes";
import { formatPct, formatSelection } from "../lib/format";
import { QueryStateView } from "./StateView";

const TOP_OPTIONS = [5, 10, 20];

/**
 * Joint (combination) probabilities. The 014 API computes these ONLY when ?bet_type=&top=K are
 * supplied (never a full trifecta grid unprompted), on the canonical field (scratched excluded +
 * renormalized). These are real model-derived probabilities — NOT pseudo.
 */
export function JointPanel({ raceId }: { raceId: string }) {
  const [betType, setBetType] = useState<string>("quinella");
  const [top, setTop] = useState<number>(10);

  const query = usePredictions(raceId, { bet_type: betType, top });

  return (
    <div className="panel">
      <h2>結合確率(券種別 上位K)</h2>
      <div className="toolbar">
        <label htmlFor="joint-bet-type">券種</label>
        <select
          id="joint-bet-type"
          value={betType}
          onChange={(e) => setBetType(e.target.value)}
        >
          {EXOTIC_BET_TYPES.map((b) => (
            <option key={b.key} value={b.key}>
              {b.label}
            </option>
          ))}
        </select>
        <label htmlFor="joint-top">上位</label>
        <select
          id="joint-top"
          value={top}
          onChange={(e) => setTop(Number(e.target.value))}
        >
          {TOP_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </div>

      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={(d) => !d.joint || d.joint.length === 0}
        loadingLabel="結合確率を計算中…"
        emptyMessage="この券種の結合確率はありません"
      >
        {(d) => (
          <table>
            <thead>
              <tr>
                <th>組み合わせ</th>
                <th className="num">的中確率</th>
              </tr>
            </thead>
            <tbody>
              {d.joint!.map((j) => (
                <tr key={formatSelection(j.selection)}>
                  <td>{formatSelection(j.selection)}</td>
                  <td className="num">{formatPct(j.prob, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryStateView>
    </div>
  );
}
