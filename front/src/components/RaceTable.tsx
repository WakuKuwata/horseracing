import { Link } from "react-router-dom";

import type { RaceSummary } from "../api/types";
import { PLACEHOLDER } from "../lib/format";

export function RaceTable({ races }: { races: RaceSummary[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>日付</th>
          <th>開催</th>
          <th className="num">R</th>
          <th>クラス</th>
          <th>コース</th>
          <th className="num">距離</th>
          <th>レースID</th>
        </tr>
      </thead>
      <tbody>
        {races.map((r) => (
          <tr key={r.race_id}>
            <td>{r.race_date ?? PLACEHOLDER}</td>
            <td>{r.venue_code ?? PLACEHOLDER}</td>
            <td className="num">{r.race_number ?? PLACEHOLDER}</td>
            <td>{r.race_class ?? PLACEHOLDER}</td>
            <td>{r.track_type ?? PLACEHOLDER}</td>
            <td className="num">{r.distance ?? PLACEHOLDER}</td>
            <td>
              <Link to={`/races/${r.race_id}`}>{r.race_id}</Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
