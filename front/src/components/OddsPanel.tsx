import { useState } from "react";

import { useOdds } from "../api/queries";
import { EXOTIC_BET_TYPES, betTypeLabel } from "../lib/betTypes";
import { formatOdds, formatSelection } from "../lib/format";
import { PseudoValue, SourceBadge } from "./PseudoValue";
import { QueryStateView } from "./StateView";

/**
 * Odds panel keeps the THREE sources in SEPARATE sections, never conflated (014/015 invariant):
 *  - win: REAL win odds (race_horses.odds) — SourceBadge real
 *  - estimated: 010 PL-extrapolated exotic odds — pseudo, only returned with ?bet_type, ALWAYS badged
 *  - real_exotic: 012 real netkeiba dividends — SourceBadge real + coverage_scope
 */
export function OddsPanel({ raceId }: { raceId: string }) {
  // estimated exotic odds require ?bet_type; default to quinella.
  const [betType, setBetType] = useState<string>("quinella");
  const query = useOdds(raceId, betType);

  return (
    <div className="panel">
      <h2>オッズ(実 / 推定 を厳密に区別)</h2>
      <div className="toolbar">
        <label htmlFor="odds-bet-type">推定/実 exotic 券種</label>
        <select
          id="odds-bet-type"
          value={betType}
          onChange={(e) => setBetType(e.target.value)}
        >
          {EXOTIC_BET_TYPES.map((b) => (
            <option key={b.key} value={b.key}>
              {b.label}
            </option>
          ))}
        </select>
      </div>

      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        loadingLabel="オッズを読み込み中…"
      >
        {(d) => (
          <>
            <h3>
              単勝(実オッズ) <SourceBadge source="real" />
            </h3>
            {d.win.length === 0 ? (
              <p className="state state--empty">単勝オッズがありません</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th className="num">馬番</th>
                    <th>馬ID</th>
                    <th className="num">単勝オッズ</th>
                  </tr>
                </thead>
                <tbody>
                  {d.win.map((w) => (
                    <tr key={w.horse_id}>
                      <td className="num">{w.horse_number ?? "—"}</td>
                      <td>{w.horse_id}</td>
                      <td className="num">{formatOdds(w.odds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {/* The API returns estimated WIN rows always + the requested exotic top-K; each row
                carries its own bet_type, so we show it per-row rather than one heading. */}
            <h3>推定オッズ(PL外挿・実オッズではない)</h3>
            {d.estimated.length === 0 ? (
              <p className="state state--empty">推定オッズがありません</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>券種</th>
                    <th>組み合わせ</th>
                    <th className="num">推定オッズ</th>
                  </tr>
                </thead>
                <tbody>
                  {d.estimated.map((e) => (
                    <tr key={`${e.bet_type}-${formatSelection(e.selection)}`}>
                      <td>{betTypeLabel(e.bet_type)}</td>
                      <td>{formatSelection(e.selection)}</td>
                      <td className="num">
                        {/* pseudo: estimated odds MUST render through PseudoValue */}
                        <PseudoValue kind="estimated">{formatOdds(e.odds)}</PseudoValue>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <h3>
              {betTypeLabel(betType)} 実配当オッズ <SourceBadge source="real" />
            </h3>
            {d.real_exotic.length === 0 ? (
              <p className="state state--empty">実配当オッズがありません</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>組み合わせ</th>
                    <th className="num">実オッズ</th>
                    <th>カバレッジ</th>
                  </tr>
                </thead>
                <tbody>
                  {d.real_exotic.map((r) => (
                    <tr key={formatSelection(r.selection)}>
                      <td>{formatSelection(r.selection)}</td>
                      <td className="num">{formatOdds(r.odds)}</td>
                      <td>
                        <SourceBadge source="real" coverageScope={r.coverage_scope} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </QueryStateView>
    </div>
  );
}
