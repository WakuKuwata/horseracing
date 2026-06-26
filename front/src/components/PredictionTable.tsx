import type { HorsePrediction } from "../api/types";
import { formatPct } from "../lib/format";

/** Per-horse win / top2 / top3 model probabilities (real model output — NOT pseudo). */
export function PredictionTable({ horses }: { horses: HorsePrediction[] }) {
  const sorted = [...horses].sort((a, b) => (b.win ?? 0) - (a.win ?? 0));
  return (
    <table>
      <thead>
        <tr>
          <th className="num">馬番</th>
          <th>馬ID</th>
          <th className="num">勝率</th>
          <th className="num">複勝率(2着内)</th>
          <th className="num">複勝率(3着内)</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((h) => (
          <tr key={h.horse_id}>
            <td className="num">{h.horse_number ?? "—"}</td>
            <td>{h.horse_id}</td>
            <td className="num">{formatPct(h.win)}</td>
            <td className="num">{formatPct(h.top2)}</td>
            <td className="num">{formatPct(h.top3)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
