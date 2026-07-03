import { Link, useParams } from "react-router-dom";

import { useCalibration, useImportance, useModels } from "../api/queries";
import { ErrorView, LoadingView, QueryStateView } from "../components/StateView";
import { formatDateTime, formatInt, formatNum, textOr } from "../lib/format";

/**
 * Feature 051 US2: model detail — metadata + adoption verdict + calibration (021) + importance
 * (040), all persisted read-only values. Unrecorded metric → the endpoint's typed 404 is shown
 * as 「未収録」 (never a blank/silent empty).
 */
export function ModelDetailPage() {
  const { modelVersion = "" } = useParams();
  const models = useModels();
  const calibration = useCalibration(modelVersion);
  const importance = useImportance(modelVersion);

  const row = models.data?.items.find((m) => m.model_version === modelVersion);

  return (
    <div className="panel">
      <p><Link to="/">← レジストリへ戻る</Link></p>
      <h1>{modelVersion}</h1>

      {models.isLoading ? <LoadingView /> : null}
      {models.error ? <ErrorView error={models.error} /> : null}
      {models.data && !row ? (
        <div className="state state--error" role="alert" data-code="model_not_found">
          モデル {modelVersion} は登録されていません
        </div>
      ) : null}

      {row ? (
        <section>
          <h2>メタデータ</h2>
          <dl className="meta">
            <div><dt>状態</dt><dd data-adoption={row.adoption_status}>{row.adoption_status}</dd></div>
            <div><dt>採用ゲート判定</dt>
              <dd>{row.adopted === null || row.adopted === undefined
                ? "—" : row.adopted ? "adopted=True(機械通過)" : "adopted=False"}</dd></div>
            <div><dt>objective / 校正</dt>
              <dd>{textOr(row.objective)} / {textOr(row.calibration)}</dd></div>
            <div><dt>特徴バージョン</dt><dd>{textOr(row.feature_version)}</dd></div>
            <div><dt>学習データ終端</dt><dd>{textOr(row.train_through)}</dd></div>
            <div><dt>学習行数</dt><dd>{formatInt(row.n_model_rows)}</dd></div>
            <div><dt>win LogLoss / AUC / ECE</dt>
              <dd>{formatNum(row.win_log_loss)} / {formatNum(row.win_auc, 4)} / {formatNum(row.win_ece)}</dd></div>
            <div><dt>git</dt><dd>{textOr(row.git_sha)}</dd></div>
            <div><dt>作成</dt><dd>{formatDateTime(row.created_at)}</dd></div>
          </dl>
        </section>
      ) : null}

      <section>
        <h2>校正(walk-forward OOS reliability)</h2>
        {calibration.error?.code === "calibration_unavailable" ? (
          <p className="state state--empty" data-code="calibration_unavailable">
            未収録(このモデルの学習時に reliability が記録されていません)
          </p>
        ) : (
          <QueryStateView
            isLoading={calibration.isLoading}
            error={calibration.error ?? null}
            data={calibration.data}
            loadingLabel="校正データを読み込み中…"
          >
            {(cal) => (
              <>
                <p className="note">ECE {formatNum(cal.ece)} / n={formatInt(cal.n_total)}</p>
                <table>
                  <thead>
                    <tr><th>予測帯</th><th className="num">予測平均</th>
                        <th className="num">実現率</th><th className="num">95%CI</th>
                        <th className="num">件数</th></tr>
                  </thead>
                  <tbody>
                    {cal.bins.map((b) => (
                      <tr key={`${b.pred_lo}-${b.pred_hi}`}>
                        <td>{formatNum(b.pred_lo, 2)}–{formatNum(b.pred_hi, 2)}</td>
                        <td className="num">{formatNum(b.pred_mean, 4)}</td>
                        <td className="num">{formatNum(b.realized_rate, 4)}</td>
                        <td className="num">
                          {formatNum(b.realized_ci_low, 4)}–{formatNum(b.realized_ci_high, 4)}
                        </td>
                        <td className="num">{formatInt(b.count)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </QueryStateView>
        )}
      </section>

      <section>
        <h2>分割利得(gain)重要度</h2>
        {importance.error?.code === "importance_unavailable" ? (
          <p className="state state--empty" data-code="importance_unavailable">
            未収録(040 以前に学習されたモデルは次回学習から記録されます)
          </p>
        ) : (
          <QueryStateView
            isLoading={importance.isLoading}
            error={importance.error ?? null}
            data={importance.data}
            loadingLabel="重要度を読み込み中…"
          >
            {(imp) => (
              <table>
                <thead>
                  <tr><th>特徴</th><th className="num">gain</th></tr>
                </thead>
                <tbody>
                  {imp.values.slice(0, 20).map((v) => (
                    <tr key={v.feature}>
                      <td>{v.feature}</td>
                      <td className="num">{formatNum(v.gain, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryStateView>
        )}
      </section>
    </div>
  );
}
