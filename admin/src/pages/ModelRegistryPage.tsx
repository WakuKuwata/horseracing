import { Link } from "react-router-dom";

import { useModels } from "../api/queries";
import { QueryStateView } from "../components/StateView";
import { formatDateTime, formatInt, formatNum, textOr } from "../lib/format";

/**
 * Feature 051 US2: model registry — which model is ACTIVE, its OOS metrics, feature/data window.
 * Values are transcriptions of persisted metrics_summary (the API never recomputes); missing
 * keys render as "—" (old models lack train_through — recorded since 050 — honestly shown).
 */
export function ModelRegistryPage() {
  const query = useModels();
  return (
    <div className="panel">
      <h1>モデルレジストリ</h1>
      <p className="note">
        指標は学習時に永続化された walk-forward OOS 評価の転記(再計算しない)。「—」は未記録。
      </p>
      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        isEmpty={(d) => d.items.length === 0}
        loadingLabel="モデル一覧を読み込み中…"
        emptyMessage="モデルがまだ登録されていません"
      >
        {(data) => (
          <table>
            <thead>
              <tr>
                <th>モデル</th>
                <th>状態</th>
                <th className="num">win LogLoss</th>
                <th className="num">AUC</th>
                <th className="num">ECE</th>
                <th>objective</th>
                <th>特徴</th>
                <th>学習データ終端</th>
                <th className="num">学習行数</th>
                <th>作成</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((m) => (
                <tr key={m.model_version} data-active={m.adoption_status === "active"}>
                  <td>
                    <Link to={`/models/${m.model_version}`}>{m.model_version}</Link>
                  </td>
                  <td>
                    <span className={`badge badge--${m.adoption_status}`}
                          data-adoption={m.adoption_status}>
                      {m.adoption_status === "active" ? "運用中" : m.adoption_status}
                    </span>
                  </td>
                  <td className="num">{formatNum(m.win_log_loss)}</td>
                  <td className="num">{formatNum(m.win_auc, 4)}</td>
                  <td className="num">{formatNum(m.win_ece)}</td>
                  <td>{textOr(m.objective)}</td>
                  <td>{textOr(m.feature_version)}</td>
                  <td>{textOr(m.train_through)}</td>
                  <td className="num">{formatInt(m.n_model_rows)}</td>
                  <td>{formatDateTime(m.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryStateView>
    </div>
  );
}
