import { useCalibration } from "../api/queries";
import { formatPct, PLACEHOLDER } from "../lib/format";
import { QueryStateView } from "./StateView";

/**
 * Feature 021 US2: walk-forward OOS reliability ("does p=30% really win ~30%?").
 *
 * Reads /models/{mv}/calibration (read-only, OOS-only). Each bin shows predicted-mean vs realized
 * rate WITH its Wilson CI and sample count (FR-006b/R5); low-count bins are flagged 件数不足 rather
 * than plotted as fact. Audit (source/OOS/model_version/期間/n/ECE) is shown on-screen so the user
 * knows this is a retrospective diagnostic over past results — not a live or in-sample number.
 */
export function CalibrationChart({ modelVersion }: { modelVersion: string | undefined }) {
  const query = useCalibration(modelVersion);

  return (
    <div className="panel">
      <h2>予測校正 (walk-forward OOS・過去結果に基づく診断)</h2>
      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={(d) => d.bins.length === 0}
        loadingLabel="校正データを読み込み中…"
        emptyMessage="このモデルの校正データはありません"
      >
        {(d) => (
          <>
            <div className="audit">
              <span>モデル: <code>{d.model_version}</code></span>
              <span>出所: <code>{d.source}{d.oos ? "(OOS)" : ""}</code></span>
              <span>期間: <code>{d.valid_years.join(", ") || PLACEHOLDER}</code></span>
              <span>件数: <code>{d.n_total}</code></span>
              <span>ECE: <code>{formatPct(d.ece)}</code></span>
            </div>
            <table>
              <thead>
                <tr>
                  <th className="num">予測確率帯</th>
                  <th className="num">予測平均</th>
                  <th className="num">実現勝率 [95%CI]</th>
                  <th className="num">件数</th>
                </tr>
              </thead>
              <tbody>
                {d.bins.map((b) => (
                  <tr key={`${b.pred_lo}-${b.pred_hi}`} data-suppressed={b.suppressed}>
                    <td className="num">
                      {formatPct(b.pred_lo, 0)}–{formatPct(b.pred_hi, 0)}
                    </td>
                    <td className="num">{formatPct(b.pred_mean)}</td>
                    <td className="num">
                      {b.suppressed ? (
                        <span className="state state--empty">件数不足</span>
                      ) : (
                        <>
                          {formatPct(b.realized_rate)}{" "}
                          <small>
                            [{formatPct(b.realized_ci_low)}–{formatPct(b.realized_ci_high)}]
                          </small>
                        </>
                      )}
                    </td>
                    <td className="num">{b.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </QueryStateView>
    </div>
  );
}
