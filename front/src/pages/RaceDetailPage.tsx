import { useState } from "react";

import { Link, useParams } from "react-router-dom";

import { usePredictions, useRace } from "../api/queries";
import { CalibrationChart } from "../components/CalibrationChart";
import { ImportanceChart } from "../components/ImportanceChart";
import { JointPanel } from "../components/JointPanel";
import { ModelSelector } from "../components/ModelSelector";
import { OddsPanel } from "../components/OddsPanel";
import { HorseEntriesTable } from "../components/HorseEntriesTable";
import { RaceDispersionPanel } from "../components/RaceDispersionPanel";
import { RaceDivergenceSummary } from "../components/RaceDivergenceSummary";
import { PredictButton } from "../components/PredictButton";
import { RecommendButton } from "../components/RecommendButton";
import { RecommendationPanel } from "../components/RecommendationPanel";
import { RefreshButton } from "../components/RefreshButton";
import { RunAuditView } from "../components/RunAudit";
import { ErrorView, LoadingView, QueryStateView } from "../components/StateView";
import { formatDateTime, PLACEHOLDER } from "../lib/format";
import { venueName } from "../lib/venues";

type Tab = "recs" | "odds" | "model";

const TABS: { key: Tab; label: string }[] = [
  { key: "recs", label: "買い目推奨" },
  { key: "odds", label: "オッズ" },
  { key: "model", label: "モデル情報" },
];

export function RaceDetailPage() {
  const { raceId = "" } = useParams();
  const raceQuery = useRace(raceId);
  // Feature 057: which model to predict with (undefined = adopted/active model, the default).
  const [modelVersion, setModelVersion] = useState<string | undefined>(undefined);
  // Per-horse predictions WITHOUT joint params (no bet_type/top) → flat win/top2/top3 only.
  const predQuery = usePredictions(raceId, undefined, modelVersion);
  const [tab, setTab] = useState<Tab>("recs");

  const pred = predQuery.data;
  const hasPreds = (pred?.horses.length ?? 0) > 0;
  // Feature 057: a selected model with no run for this race → typed 404 (未生成), distinct from
  // loading / empty / other errors (a 503 with the same code is a real fetch error → alert).
  const modelUnavailable =
    predQuery.error?.status === 404 &&
    predQuery.error?.code === "prediction_unavailable";

  return (
    <section>
      <div className="detail-topbar">
        <Link to="/">← レース一覧</Link>
        {/* Ordered pipeline: refresh data → predict → recommend (024/028/043 ops writes). */}
        <div className="detail-actions">
          <RefreshButton raceId={raceId} />
          <PredictButton raceId={raceId} />
          <RecommendButton raceId={raceId} />
        </div>
      </div>

      <div className="panel">
        <QueryStateView
          isLoading={raceQuery.isLoading}
          error={raceQuery.error ?? null}
          data={raceQuery.data}
          loadingLabel="レース情報を読み込み中…"
        >
          {(r) => (
            <>
              <h2 className="race-title">
                {(r.race_name ?? "").replace(/\*+$/, "") || r.race_class || PLACEHOLDER}
              </h2>
              <div className="race-meta">
                <span>{r.race_date ?? PLACEHOLDER}</span>
                <span>{venueName(r.venue_code)} {r.race_number ?? ""}R</span>
                <span>{r.race_class ?? PLACEHOLDER}</span>
                <span>{r.track_type ?? ""}{r.distance != null ? ` ${r.distance}m` : ""}</span>
                <span>{r.horses.length}頭</span>
              </div>
            </>
          )}
        </QueryStateView>
      </div>

      <div className="panel">
        <h2>出走表</h2>
        <QueryStateView
          isLoading={raceQuery.isLoading}
          error={raceQuery.error ?? null}
          data={raceQuery.data}
          isEmpty={(d) => d.horses.length === 0}
          loadingLabel="出走表を読み込み中…"
          emptyMessage="出走馬の情報がありません"
        >
          {(r) => (
            <>
              {/* Feature 057: model switcher (only when this race has predictions from >1 model). */}
              {(pred?.available_models?.length ?? 0) > 0 && (
                <ModelSelector
                  models={pred?.available_models ?? []}
                  selected={modelVersion}
                  onChange={setModelVersion}
                />
              )}
              {predQuery.isLoading && <LoadingView label="予測を読み込み中…" />}
              {/* Feature 057: selected model has no prediction for this race — a distinct state. */}
              {modelUnavailable ? (
                <p className="state state--empty" data-testid="model-unavailable">
                  選択したモデルはこのレースをまだ予測していません。別のモデルを選ぶか、予測を生成してください。
                </p>
              ) : (
                predQuery.error && <ErrorView error={predQuery.error} />
              )}
              {pred?.run && <RunAuditView run={pred.run} />}
              {/* Feature 066 axis A: race-level 荒れ度 readout (market-q dispersion, display-only). */}
              {hasPreds && <RaceDispersionPanel dispersion={pred?.race_dispersion} />}
              {/* Feature 066 axis B: neutral model-vs-market divergence summary (人気/穴の材料). */}
              {hasPreds && <RaceDivergenceSummary divergence={pred?.race_divergence} />}
              {/* 予測なし → 空の列を並べず生成導線を示す(read-onlyの表示は永続データのみ) */}
              {predQuery.isSuccess && !hasPreds && (
                <p className="state state--empty" data-testid="no-predictions-cta">
                  このレースの予測はまだ生成されていません。右上の「予測する」で生成できます。
                </p>
              )}
              {hasPreds && pred?.canonical_consistent === false && (
                <p className="state state--empty" data-testid="pq-incomparable">
                  モデル勝率と市場評価の母集団が一致しないため、「市場との差」は表示しません(比較不可)。
                </p>
              )}
              <HorseEntriesTable
                entries={r.horses}
                predictions={pred?.horses ?? []}
                oddsAsOf={pred?.odds_as_of}
                canonicalConsistent={pred?.canonical_consistent}
              />
              <p className="table-hint">
                列見出しをクリックで並び替え（市場評価＝単勝オッズ由来の推定値・実測ではありません）。
                「寄与」でモデルの判断要因を表示。
              </p>
              {hasPreds && (
                <>
                  <p className="table-hint" data-testid="market-superiority-note">
                    ※ 市場評価は実データでモデル勝率より win 予測が上手いことが確認されています(020)。
                    「市場との差」は見解の相違（<span className="diff--model_higher">青=モデルが高い</span>
                    ・<span className="diff--market_higher">紫=市場が高い</span>）であり、
                    買い目の推奨ではありません。
                  </p>
                  <div className="audit">
                    <span>
                      オッズ時刻: <code>{formatDateTime(pred?.odds_as_of)}</code>
                    </span>
                    <span>
                      オッズ種別: <code>{pred?.odds_source ?? PLACEHOLDER}</code>
                    </span>
                    <span>
                      q 出所: <code>{pred?.market_prob_source ?? PLACEHOLDER}</code>
                    </span>
                  </div>
                </>
              )}
            </>
          )}
        </QueryStateView>
      </div>

      <div className="tabbar" role="tablist" aria-label="詳細情報">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "recs" && <RecommendationPanel raceId={raceId} />}
      {tab === "odds" && <OddsPanel raceId={raceId} />}
      {tab === "model" && (
        <>
          <CalibrationChart modelVersion={pred?.run?.model_version} />
          <ImportanceChart modelVersion={pred?.run?.model_version} />
          <JointPanel raceId={raceId} />
        </>
      )}
    </section>
  );
}
