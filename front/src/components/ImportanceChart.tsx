import { useImportance } from "../api/queries";
import { formatNum } from "../lib/format";
import { featureLabel } from "./featureLabels";
import { QueryStateView } from "./StateView";

// Feature 040 US2: global SPLIT-GAIN (gain) importance for the active model.
// Labelled narrowly as "分割利得(gain)重要度" — gain is biased toward high-gain-split features, so
// this is NOT presented as general "feature importance". Read-only from metrics_summary; a model
// with no recorded importance shows a typed "未収録" state, not an error.

const TOP_N = 20;

export function ImportanceChart({ modelVersion }: { modelVersion: string | undefined }) {
  const query = useImportance(modelVersion);

  // "未収録" is a typed 404 (importance_unavailable), NOT a failure — show a friendly state
  // distinct from the error screen (US2 AC2). Real errors still surface via QueryStateView.
  const unavailable = query.error?.code === "importance_unavailable";

  return (
    <div className="panel">
      <h2>モデルの分割利得（gain）重要度</h2>
      {unavailable && (
        <div className="state state--empty" data-state="empty">
          このモデルには重要度が収録されていません
        </div>
      )}
      {!unavailable && (
      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={(d) => d.values.length === 0}
        loadingLabel="重要度を読み込み中…"
        emptyMessage="このモデルには重要度が収録されていません"
      >
        {(d) => {
          const top = d.values.slice(0, TOP_N);
          const max = top.reduce((m, v) => Math.max(m, v.gain), 0);
          return (
            <>
              <div className="audit">
                <span>モデル: <code>{d.model_version}</code></span>
                <span>指標: <code>分割利得(gain)</code></span>
              </div>
              <table className="importance-table">
                <tbody>
                  {top.map((v) => (
                    <tr key={v.feature}>
                      <td className="imp-feature">{featureLabel(v.feature).label}</td>
                      <td className="imp-bar-cell">
                        <span className="imp-bar" aria-hidden="true">
                          <span
                            className="imp-fill"
                            style={{ width: `${max > 0 ? (v.gain / max) * 100 : 0}%` }}
                          />
                        </span>
                      </td>
                      <td className="imp-num num">{formatNum(v.gain, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="table-hint">
                ※ gain 重要度は分割利得の大きい特徴に偏ります。一般的な「特徴の重要さ」とは限りません。
              </p>
            </>
          );
        }}
      </QueryStateView>
      )}
    </div>
  );
}
