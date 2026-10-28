import { useState } from "react";

import { usePredictions } from "../api/queries";
import type { RaceDetail } from "../api/types";
import { formatDateTime, PLACEHOLDER } from "../lib/format";
import { CalibrationChart } from "./CalibrationChart";
import { HorseEntriesTable } from "./HorseEntriesTable";
import { ImportanceChart } from "./ImportanceChart";
import { JointPanel } from "./JointPanel";
import { ModelMarketStanding } from "./ModelMarketStanding";
import { ModelSelector } from "./ModelSelector";
import { OddsFreshness } from "./OddsFreshness";
import { PredictButton } from "./PredictButton";
import { RaceDispersionPanel } from "./RaceDispersionPanel";
import { RaceDivergenceSummary } from "./RaceDivergenceSummary";
import { RecommendButton } from "./RecommendButton";
import { RecommendationPanel } from "./RecommendationPanel";
import { RunAuditView } from "./RunAudit";
import { ErrorView, LoadingView } from "./StateView";

/** Old prediction and betting-policy outputs retained for comparison with the ROI view. */
export function LegacyPredictionComparison({ raceId, race }: { raceId: string; race?: RaceDetail }) {
  const [modelVersion, setModelVersion] = useState<string | undefined>();
  const [modelInfoOpen, setModelInfoOpen] = useState(false);
  const query = usePredictions(raceId, undefined, modelVersion);
  const pred = query.data;
  const hasPreds = (pred?.horses.length ?? 0) > 0;
  const unavailable = query.error?.status === 404 && query.error.code === "prediction_unavailable";

  return (
    <>
      <section className="panel legacy-comparison" aria-labelledby="legacy-comparison-title">
        <div className="legacy-comparison__heading">
          <h2 id="legacy-comparison-title">予測モデルとの比較</h2>
          <span className="badge">比較用</span>
        </div>
        <p className="note" data-testid="legacy-comparison-note">
          旧予測モデルの勝率・判断要因と、旧方策の買い目・配分を比較するための情報です。
          上の期待回収率とは別に計算しています。
        </p>
        <p className="note" data-testid="comparison-generation-scope">
          モデル切替は予測の比較表示だけに適用され、期待回収率は変わりません。
          「予測する」「買い目生成」は採用モデルを使用します。
        </p>
        <div className="comparison-actions" aria-label="比較データの生成">
          <PredictButton raceId={raceId} />
          <RecommendButton raceId={raceId} />
        </div>
        {(pred?.available_models?.length ?? 0) > 0 && (
          <ModelSelector models={pred?.available_models ?? []} selected={modelVersion} onChange={setModelVersion} />
        )}
        {query.isLoading && <LoadingView label="比較用の予測を読み込み中…" />}
        {unavailable ? (
          <p className="state state--empty" data-testid="model-unavailable">
            選択したモデルはこのレースをまだ予測していません。生成操作は採用モデルだけに適用されます。
            {modelVersion && (
              <button type="button" onClick={() => setModelVersion(undefined)}>
                採用モデルの表示へ戻る
              </button>
            )}
          </p>
        ) : query.error && <ErrorView error={query.error} />}
        {query.isSuccess && !hasPreds && (
          <p className="state state--empty" data-testid="no-predictions-cta">
            このレースの比較用の予測はまだ生成されていません。「予測する」で生成できます。
          </p>
        )}
        {pred?.run && <RunAuditView run={pred.run} />}
        {/* B9: the 084 chaos readout moved to the ROI view (/races/{id}/chaos); not repeated here. */}
        {hasPreds && <RaceDispersionPanel dispersion={pred?.race_dispersion} />}
        {hasPreds && <RaceDivergenceSummary divergence={pred?.race_divergence} />}
        {hasPreds && pred?.canonical_consistent === false && (
          <p className="state state--empty" data-testid="pq-incomparable">
            モデル勝率と市場評価の母集団が一致しないため、「市場との差」は表示しません(比較不可)。
          </p>
        )}
        <ModelMarketStanding canonicalConsistent={pred?.canonical_consistent} />
        <OddsFreshness oddsAsOf={pred?.odds_as_of} postTime={race?.post_time} hasResults={race?.has_results} />
        {race && (
          <HorseEntriesTable entries={race.horses} predictions={pred?.horses ?? []}
            oddsAsOf={pred?.odds_as_of} canonicalConsistent={pred?.canonical_consistent} />
        )}
        <p className="table-hint">
          初期表示はモデル勝率順です。「寄与」で旧モデルの判断要因を表示。
          市場評価は単勝オッズ由来の推定値です。
        </p>
        {hasPreds && (
          <>
            <p className="table-hint" data-testid="divergence-legend">
              「市場との差」は見解の相違（<span className="diff--model_higher">青=モデルが高い</span>
              ・<span className="diff--market_higher">紫=市場が高い</span>）であり、買い目の推奨ではありません。
            </p>
            <div className="audit">
              <span>オッズ時刻: <code>{formatDateTime(pred?.odds_as_of)}</code></span>
              <span>オッズ種別: <code>{pred?.odds_source ?? PLACEHOLDER}</code></span>
              <span>q 出所: <code>{pred?.market_prob_source ?? PLACEHOLDER}</code></span>
            </div>
          </>
        )}
      </section>
      <RecommendationPanel raceId={raceId} raceDate={race?.race_date ?? undefined} entries={race?.horses} comparisonMode />
      <details className="panel comparison-model-info" onToggle={(event) => setModelInfoOpen(event.currentTarget.open)}>
        <summary>モデル情報・結合確率</summary>
        {modelInfoOpen && (
          <>
            <CalibrationChart modelVersion={pred?.run?.model_version} />
            <ImportanceChart modelVersion={pred?.run?.model_version} />
            <JointPanel raceId={raceId} modelVersion={modelVersion} />
          </>
        )}
      </details>
    </>
  );
}
