import { Link, useParams } from "react-router-dom";

import { usePredictions, useRace } from "../api/queries";
import { CalibrationChart } from "../components/CalibrationChart";
import { JointPanel } from "../components/JointPanel";
import { OddsPanel } from "../components/OddsPanel";
import { HorseEntriesTable } from "../components/HorseEntriesTable";
import { PQCompare } from "../components/PQCompare";
import { PredictButton } from "../components/PredictButton";
import { RecommendationPanel } from "../components/RecommendationPanel";
import { RefreshButton } from "../components/RefreshButton";
import { RunAuditView } from "../components/RunAudit";
import { QueryStateView } from "../components/StateView";
import { PLACEHOLDER } from "../lib/format";
import { venueName } from "../lib/venues";

export function RaceDetailPage() {
  const { raceId = "" } = useParams();
  const raceQuery = useRace(raceId);
  // Per-horse predictions WITHOUT joint params (no bet_type/top) → flat win/top2/top3 only.
  const predQuery = usePredictions(raceId);

  return (
    <section>
      <div className="detail-topbar">
        <Link to="/">← レース一覧</Link>
        {/* US1: refresh THIS race from netkeiba (ops write service); display stays on 014. */}
        <RefreshButton raceId={raceId} />
        {/* 028: generate THIS race's model predictions on demand (ops write → 014 refetch). */}
        <PredictButton raceId={raceId} />
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
              {predQuery.data?.run && <RunAuditView run={predQuery.data.run} />}
              <HorseEntriesTable entries={r.horses} predictions={predQuery.data?.horses ?? []} />
              <p className="table-hint">
                列見出しをクリックで並び替え（勝率p＝モデル予測 / 市場q＝オッズ由来・疑似）
              </p>
            </>
          )}
        </QueryStateView>
      </div>

      <div className="panel">
        <h2>モデル予測 p と市場推定 q の比較</h2>
        <QueryStateView
          isLoading={predQuery.isLoading}
          error={predQuery.error ?? null}
          data={predQuery.data}
          isEmpty={(d) => d.horses.length === 0}
          loadingLabel="予測を読み込み中…"
          emptyMessage="この レースの予測はありません"
        >
          {(d) => <PQCompare data={d} />}
        </QueryStateView>
      </div>

      <CalibrationChart modelVersion={predQuery.data?.run?.model_version} />

      <JointPanel raceId={raceId} />
      <OddsPanel raceId={raceId} />
      <RecommendationPanel raceId={raceId} />
    </section>
  );
}
