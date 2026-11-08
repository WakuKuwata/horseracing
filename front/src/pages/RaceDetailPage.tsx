import { useState } from "react";
import type { KeyboardEvent } from "react";

import { Link, useParams } from "react-router-dom";

import { useAttention, useMarketEv, usePredictions, useRace, useRaceChaos } from "../api/queries";
import { AttentionNote } from "../components/AttentionNote";
import { AttentionRulesPanel } from "../components/AttentionRulesPanel";
import { ExpectedReturnNote } from "../components/ExpectedReturnNote";
import { ForecastSummary } from "../components/ForecastSummary";
import { HorseEntriesTable } from "../components/HorseEntriesTable";
import { LegacyPredictionComparison } from "../components/LegacyPredictionComparison";
import { OddsPanel } from "../components/OddsPanel";
import { PredictButton } from "../components/PredictButton";
import { RaceChaosPanel } from "../components/RaceChaosPanel";
import { RefreshButton } from "../components/RefreshButton";
import { RoiPurchaseRecord } from "../components/RoiPurchaseRecord";
import { QueryStateView } from "../components/StateView";
import { formatJstDateTime, PLACEHOLDER } from "../lib/format";
import { useForecastMarks } from "../lib/useForecastMarks";
import { useForecastLiveRefresh } from "../lib/useForecastLiveRefresh";
import { venueName } from "../lib/venues";

type Tab = "odds" | "comparison";
const TABS: { key: Tab; label: string }[] = [
  { key: "odds", label: "オッズ" },
  { key: "comparison", label: "予測モデルとの比較" },
];

export function RaceDetailPage() {
  const { raceId = "" } = useParams();
  return <RaceDetailContent key={raceId} raceId={raceId} />;
}

function RaceDetailContent({ raceId }: { raceId: string }) {
  const raceQuery = useRace(raceId);
  useForecastLiveRefresh(raceId, raceQuery.data);
  const marketEvQuery = useMarketEv(raceId);
  const attentionQuery = useAttention(raceId);
  const predictionQuery = usePredictions(raceId);
  const { marks, error: marksError, setMark } = useForecastMarks(raceId);
  const prediction = predictionQuery.data;
  const predictionUnavailable = (predictionQuery.error?.status === 404
    && predictionQuery.error.code === "prediction_unavailable") || prediction?.horses.length === 0;
  const model = prediction?.available_models?.find((item) => item.is_selected);
  // B9: the 084 chaos readout (market-only) comes from its own endpoint, so the ROI view shows it
  // without touching the legacy /predictions response.
  const chaosQuery = useRaceChaos(raceId);
  const [tab, setTab] = useState<Tab>("odds");
  const [comparisonOpened, setComparisonOpened] = useState(false);

  function selectTab(next: Tab) {
    setTab(next);
    if (next === "comparison") setComparisonOpened(true);
  }

  function navigateTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index + TABS.length - 1) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    selectTab(TABS[next].key);
    document.getElementById(`detail-tab-${TABS[next].key}`)?.focus();
  }

  return (
    <section>
      <div className="detail-topbar">
        <Link to="/">← レース一覧</Link>
        <div className="detail-actions"><RefreshButton raceId={raceId} /></div>
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

      <section className="panel roi-overview" aria-labelledby="roi-overview-title">
        <div className="roi-overview__heading">
          <h2 id="roi-overview-title">予想・出走表</h2>
          <span className="badge">期待回収率は検証中</span>
        </div>
        <p className="note">
          勝率・連対率（2着以内）・複勝率（3着以内）と期待回収率を見比べ、自分の印を付けて予想できます。
          勝率順は勝つ可能性の順です。期待回収率は別の市場連動モデルで計算しています。
        </p>
        <div className="forecast-toolbar">
          <div className="forecast-model-status" aria-live="polite">
            {predictionQuery.isLoading && <p className="note">確率予測を読み込み中…</p>}
            {predictionUnavailable && <p className="note">確率予測は未生成です。「予測する」で採用モデルの予測を生成できます。</p>}
            {predictionQuery.error && !predictionUnavailable && (
              <p className="note">確率予測を取得できませんでした（HTTP {predictionQuery.error.status}）。出走表と印付けは引き続き使えます。</p>
            )}
            {prediction && !predictionUnavailable && (
              <p className="note" data-testid="forecast-model-info">
                確率予測: {model?.display_name ?? prediction.run?.model_version ?? PLACEHOLDER}
                {model?.display_name && prediction.run && !model.display_name.includes(prediction.run.model_version) && ` (${prediction.run.model_version})`}
                {" ／ 計算日時: "}{formatJstDateTime(prediction.run?.computed_at) ?? PLACEHOLDER}
              </p>
            )}
          </div>
          <PredictButton raceId={raceId} />
        </div>
        <p className="note forecast-storage-note">
          自分の印はこのブラウザに自動保存されます。他の端末とは共有されません。
          ◎ 本命 ／ ○ 対抗 ／ ▲ 単穴 ／ △ 連下 ／ ☆ 注目 ／ 消 見送り
        </p>
        {marksError && <p className="note" role="status">{marksError}</p>}
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
              <ForecastSummary
                raceId={raceId}
                entries={r.horses}
                prediction={prediction}
                marketEv={marketEvQuery.data}
                predictionLoading={predictionQuery.isLoading}
                predictionFailed={!!predictionQuery.error}
                marketEvLoading={marketEvQuery.isLoading}
                marketEvFailed={!!marketEvQuery.error}
              />
              <details className="forecast-notes" open={marketEvQuery.data?.status !== "available" || !!marketEvQuery.error || !!attentionQuery.error}>
                <summary>期待回収率・注目条件の説明と計算時刻</summary>
                <ExpectedReturnNote
                  marketEv={marketEvQuery.data}
                  isLoading={marketEvQuery.isLoading}
                  error={marketEvQuery.error ?? null}
                />
                <p className="note" data-testid="prospective-link">
                  <Link to="/market-ev/prospective">発走前オッズでの前向き現況(公式払戻で精算)を見る</Link>
                </p>
                <AttentionNote error={attentionQuery.error ?? null} isLoading={attentionQuery.isLoading} />
              </details>
              <HorseEntriesTable
                variant="forecast"
                entries={r.horses}
                predictions={prediction?.horses ?? []}
                marketEv={marketEvQuery.data}
                attention={attentionQuery.data}
                marks={marks}
                onMarkChange={setMark}
              />
              <p className="table-hint">
                初期表示は単勝オッズの低い順（オッズがない場合は馬番順）です。
                列見出しで並び替えできます。勝率・連対率・複勝率は最初のクリックで高い順に表示します。
                狭い画面では表を横にスクロールできます。
                {attentionQuery.data?.status === "available" && "注目条件の「▸」で注目条件の内訳を表示。"}
              </p>
              <AttentionRulesPanel />
              {/* B9: 上位3着の荒れ度(084・市場 q のみ)— 予測モデルとは無関係なので主画面に置く。
                  注目条件の一覧は表のヒントの直後(138 FR)なので、その後ろに置く。 */}
              <RaceChaosPanel
                chaos={chaosQuery.data?.race_chaos}
                isLoading={chaosQuery.isLoading}
                error={chaosQuery.error ?? null}
              />
              <p className="table-hint" data-testid="roi-odds-context">
                単勝欄は現在保存されているオッズです。期待回収率は計算時のオッズが基準です。
                計算済みの場合、使用したオッズの取得時刻と計算時刻を上の注記に表示しています。
              </p>
            </>
          )}
        </QueryStateView>
      </section>

      {/* B6: record the actual purchase/skip from the ROI view (106 stream; no recommendation). */}
      {raceQuery.data && (
        <RoiPurchaseRecord
          raceId={raceId}
          raceDate={raceQuery.data.race_date ?? undefined}
          horseNumbers={raceQuery.data.horses
            .map((h) => h.horse_number)
            .filter((n): n is number => typeof n === "number")}
          hasResults={raceQuery.data.has_results === true}
        />
      )}

      <div className="tabbar" role="tablist" aria-label="詳細情報">
        {TABS.map((item, index) => (
          <button
            key={item.key}
            id={`detail-tab-${item.key}`}
            type="button"
            role="tab"
            aria-controls={`detail-panel-${item.key}`}
            aria-selected={tab === item.key}
            tabIndex={tab === item.key ? 0 : -1}
            onClick={() => selectTab(item.key)}
            onKeyDown={(event) => navigateTab(event, index)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div id="detail-panel-odds" role="tabpanel" aria-labelledby="detail-tab-odds" hidden={tab !== "odds"}>
        <OddsPanel raceId={raceId} />
      </div>
      <div
        id="detail-panel-comparison"
        role="tabpanel"
        aria-labelledby="detail-tab-comparison"
        hidden={tab !== "comparison"}
      >
        {/* Keep controls mounted after first visit: hidden tabs still track active jobs. */}
        {comparisonOpened && <LegacyPredictionComparison raceId={raceId} race={raceQuery.data} />}
      </div>
    </section>
  );
}
