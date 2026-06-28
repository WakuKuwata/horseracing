import { Link, useParams } from "react-router-dom";

import { usePredictions, useRace } from "../api/queries";
import { CalibrationChart } from "../components/CalibrationChart";
import { JointPanel } from "../components/JointPanel";
import { OddsPanel } from "../components/OddsPanel";
import { PQCompare } from "../components/PQCompare";
import { PredictionTable } from "../components/PredictionTable";
import { RecommendationPanel } from "../components/RecommendationPanel";
import { RunAuditView } from "../components/RunAudit";
import { QueryStateView } from "../components/StateView";
import { PLACEHOLDER } from "../lib/format";

export function RaceDetailPage() {
  const { raceId = "" } = useParams();
  const raceQuery = useRace(raceId);
  // Per-horse predictions WITHOUT joint params (no bet_type/top) → flat win/top2/top3 only.
  const predQuery = usePredictions(raceId);

  return (
    <section>
      <p>
        <Link to="/">← レース一覧</Link>
      </p>

      <div className="panel">
        <QueryStateView
          isLoading={raceQuery.isLoading}
          error={raceQuery.error ?? null}
          data={raceQuery.data}
          loadingLabel="レース情報を読み込み中…"
        >
          {(r) => (
            <>
              <h2>
                {r.race_date ?? PLACEHOLDER} {r.venue_code ?? ""} {r.race_number ?? ""}R
              </h2>
              <div className="audit">
                <span>ID: <code>{r.race_id}</code></span>
                <span>クラス: <code>{r.race_class ?? PLACEHOLDER}</code></span>
                <span>コース: <code>{r.track_type ?? PLACEHOLDER}</code></span>
                <span>距離: <code>{r.distance ?? PLACEHOLDER}</code></span>
                <span>頭数: <code>{r.horses.length}</code></span>
              </div>
            </>
          )}
        </QueryStateView>
      </div>

      <div className="panel">
        <h2>勝率・複勝率(モデル予測)</h2>
        <QueryStateView
          isLoading={predQuery.isLoading}
          error={predQuery.error ?? null}
          data={predQuery.data}
          isEmpty={(d) => d.horses.length === 0}
          loadingLabel="予測を読み込み中…"
          emptyMessage="この レースの予測はありません"
        >
          {(d) => (
            <>
              {d.run && <RunAuditView run={d.run} />}
              <PredictionTable horses={d.horses} />
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
