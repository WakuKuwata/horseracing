import { Link } from "react-router-dom";

import type { HorseEntry, HorseMarketEv, HorsePrediction, MarketEvResponse, PredictionResponse } from "../api/types";
import { formatJstDateTime, formatPct, PLACEHOLDER } from "../lib/format";
import { PseudoValue } from "./PseudoValue";

type SummaryHorse = HorseEntry & { prediction?: HorsePrediction; roi?: HorseMarketEv };
const PROBABILITY_TOLERANCE = 1e-6;

function probability(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function expectedReturn(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function sameField(rows: { horse_id: string }[], started: HorseEntry[]) {
  const ids = new Set(rows.map((horse) => horse.horse_id));
  return ids.size === rows.length && rows.length === started.length
    && new Set(started.map((horse) => horse.horse_id)).size === started.length
    && started.every((horse) => ids.has(horse.horse_id));
}

function headsConsistent(prediction: HorsePrediction | undefined) {
  if (!probability(prediction?.win)) return false;
  const { win, top2, top3 } = prediction;
  if (top2 != null && (!probability(top2) || win > top2 + PROBABILITY_TOLERANCE)) return false;
  if (top3 != null && (!probability(top3) || win > top3 + PROBABILITY_TOLERANCE)) return false;
  return top2 == null || top3 == null || top2 <= top3 + PROBABILITY_TOLERANCE;
}

function sumMatches(values: number[], total: number) {
  return Math.abs(values.reduce((sum, value) => sum + value, 0) - total) <= PROBABILITY_TOLERANCE;
}

function horseOrder(a: SummaryHorse, b: SummaryHorse) {
  return (a.horse_number ?? Number.MAX_SAFE_INTEGER) - (b.horse_number ?? Number.MAX_SAFE_INTEGER)
    || a.horse_id.localeCompare(b.horse_id);
}

function HorseFacts({ horse, rate, showRoi = true }: { horse: SummaryHorse; rate?: "win" | "top3"; showRoi?: boolean }) {
  const value = rate ? horse.prediction?.[rate] : null;
  return (
    <>
      <Link className="forecast-summary__horse" to={`/horses/${horse.horse_id}`}>
        {horse.horse_number ?? PLACEHOLDER}番 {horse.horse_name ?? horse.horse_id}
      </Link>
      <span className="forecast-summary__values">
        {rate && probability(value) && <span>{rate === "win" ? "勝率" : "3着以内率"} {formatPct(value)}</span>}
        {showRoi && expectedReturn(horse.roi?.expected_return) && (
          <span>期待回収率 <PseudoValue kind="expected_return">{formatPct(horse.roi.expected_return)}</PseudoValue></span>
        )}
      </span>
    </>
  );
}

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Read-only summaries of saved outputs. No probability blending or betting-policy inference. */
export function ForecastSummary({
  raceId,
  entries,
  prediction,
  marketEv,
  predictionLoading = false,
  predictionFailed = false,
  marketEvLoading = false,
  marketEvFailed = false,
}: {
  raceId: string;
  entries: HorseEntry[];
  prediction?: PredictionResponse | null;
  marketEv?: MarketEvResponse | null;
  predictionLoading?: boolean;
  predictionFailed?: boolean;
  marketEvLoading?: boolean;
  marketEvFailed?: boolean;
}) {
  const validPrediction = !predictionFailed && prediction?.race_id === raceId ? prediction : null;
  const validRoi = !marketEvFailed && marketEv?.race_id === raceId && marketEv.status === "available" ? marketEv : null;
  const predictions = new Map(validPrediction?.horses.map((horse) => [horse.horse_id, horse]) ?? []);
  const rois = new Map(validRoi?.horses.map((horse) => [horse.horse_id, horse]) ?? []);
  const started = entries.filter((horse) => horse.entry_status === "started");
  const horses: SummaryHorse[] = started.map((horse) => ({
    ...horse,
    prediction: predictions.get(horse.horse_id),
    roi: rois.get(horse.horse_id),
  }));
  const win = horses.filter((horse) => probability(horse.prediction?.win)).sort((a, b) =>
    b.prediction!.win! - a.prediction!.win! || horseOrder(a, b),
  );
  const top3 = horses.filter((horse) => probability(horse.prediction?.top3)).sort((a, b) =>
    b.prediction!.top3! - a.prediction!.top3! || horseOrder(a, b),
  );
  const roi = horses.filter((horse) => expectedReturn(horse.roi?.expected_return)).sort((a, b) =>
    b.roi!.expected_return - a.roi!.expected_return || horseOrder(a, b),
  );
  // The API can filter a saved run to today's started entries without recomputing its old
  // probabilities. Coverage alone is therefore insufficient after a scratch: check field,
  // probability masses and ordering before deriving any candidate or cross-model category.
  const knownTop2 = horses.map((horse) => horse.prediction?.top2).filter(probability);
  const top2MassValid = knownTop2.length !== horses.length
    || sumMatches(knownTop2, Math.min(2, horses.length));
  const top3MassValid = top3.length === 0 || (top3.length === horses.length
    && sumMatches(top3.map((horse) => horse.prediction!.top3!), Math.min(3, horses.length)));
  const fullWin = horses.length > 0 && !!validPrediction
    && sameField(validPrediction.horses, started)
    && horses.every((horse) => headsConsistent(horse.prediction))
    && sumMatches(win.map((horse) => horse.prediction!.win!), 1)
    && top2MassValid && top3MassValid;
  const fullTop3 = fullWin && top3.length === horses.length
    && sumMatches(top3.map((horse) => horse.prediction!.top3!), Math.min(3, horses.length));
  const fullRoi = horses.length > 0 && !!validRoi && sameField(validRoi.horses, started)
    && roi.length === horses.length;
  const winLeaders = new Set(win.slice(0, 3).map((horse) => horse.horse_id));
  // These are transparent display categories, not newly selected buy conditions or a score.
  const leaderBelowPar = fullWin && fullRoi ? win.slice(0, 3).find((horse) =>
    expectedReturn(horse.roi?.expected_return) && horse.roi.expected_return <= 1,
  ) : undefined;
  const otherAbovePar = fullWin && fullRoi ? roi.find((horse) =>
    !winLeaders.has(horse.horse_id) && horse.roi!.expected_return > 1,
  ) : undefined;
  const predictionState = predictionLoading ? "確率予測を読み込み中です。"
    : predictionFailed ? "確率予測を取得できませんでした。"
      : "確率予測はまだありません。生成後に候補を表示します。";
  const roiState = marketEvLoading ? "期待回収率を読み込み中です。"
    : marketEvFailed ? "期待回収率を取得できませんでした。"
      : validRoi ? "有効な期待回収率がありません。"
        : "期待回収率は未計算または再計算待ちです。";
  const predictionPending = !!validPrediction?.horses.length && !fullWin;
  const roiPending = !!validRoi && !fullRoi;
  const predictionTime = timestamp(validPrediction?.run?.computed_at);
  const roiTime = timestamp(validRoi?.computed_at);
  const predictionOddsTime = timestamp(validPrediction?.odds_as_of);
  const roiOddsTime = timestamp(validRoi?.odds_observed_at);
  const timesKnown = [predictionTime, roiTime, predictionOddsTime, roiOddsTime].every((time) => time !== null);
  const differentTimes = timesKnown && (predictionTime !== roiTime || predictionOddsTime !== roiOddsTime);

  return (
    <section className="forecast-summary" aria-label="自動予想サマリー">
      <h3>自動予想サマリー</h3>
      <p className="forecast-summary__note">保存済みの予測から、勝つ可能性・上位入着・配当との関係を整理しています。</p>
      <div className="forecast-summary__grid">
        <section className="forecast-summary__card" aria-label="勝利候補">
          <h4>勝利候補</h4>
          <p className="forecast-summary__note">勝率が高い順に最大3頭</p>
          {fullWin ? <ol className="forecast-summary__list">
            {win.slice(0, 3).map((horse) => <li key={horse.horse_id}><HorseFacts horse={horse} rate="win" showRoi={fullRoi} /></li>)}
          </ol> : <p className="forecast-summary__note">{predictionPending
            ? "出走構成に対応する確率の再計算待ちです。保存予測の全頭・確率の整合性を確認できません。"
            : predictionState}</p>}
        </section>
        <section className="forecast-summary__card" aria-label="上位入着候補">
          <h4>上位入着候補</h4>
          <p className="forecast-summary__note">3着以内率が高い順に最大3頭</p>
          {fullTop3 ? <ol className="forecast-summary__list">
            {top3.slice(0, 3).map((horse) => <li key={horse.horse_id}><HorseFacts horse={horse} rate="top3" showRoi={fullRoi} /></li>)}
          </ol> : <p className="forecast-summary__note">{predictionPending || (fullWin && top3.length > 0)
            ? "出走構成に対応する3着以内確率の再計算待ちです。保存予測の全頭・確率の整合性を確認できません。"
            : fullWin ? "3着以内の予測はまだありません。" : predictionState}</p>}
        </section>
        <section className="forecast-summary__card" aria-label="配当との比較">
          <h4>配当との比較</h4>
          {fullRoi ? <>
            {roi[0].horse_id !== otherAbovePar?.horse_id && roi[0].horse_id !== leaderBelowPar?.horse_id && <>
              <p className="forecast-summary__note">期待回収率が最も高い馬</p>
              <div className="forecast-summary__comparison"><HorseFacts horse={roi[0]} /></div>
            </>}
            {leaderBelowPar && <div className="forecast-summary__comparison">
              <p className="forecast-summary__note" title="勝率上位3頭・期待回収率100%以下のうち勝率が最も高い馬">勝率上位の配当評価（期待回収率100%以下）</p>
              <HorseFacts horse={leaderBelowPar} rate="win" />
            </div>}
            {otherAbovePar && <div className="forecast-summary__comparison">
              <p className="forecast-summary__note" title="勝率上位3頭以外・期待回収率100%超のうち期待回収率が最も高い馬">勝率上位3頭以外の配当評価（期待回収率100%超）</p>
              <HorseFacts horse={otherAbovePar} rate="win" />
            </div>}
            {!fullWin && <p className="forecast-summary__note">全頭の勝率の整合性を確認できると、勝率の順位と期待回収率を比較できます。</p>}
            {fullWin && fullRoi && !leaderBelowPar && !otherAbovePar && <p className="forecast-summary__note">今回の表示条件に該当する比較対象はありません。</p>}
          </> : <p className="forecast-summary__note">{roiPending
            ? "出走構成に対応する期待回収率の再計算待ちです。全頭の保存値の整合性を確認できません。"
            : roiState}</p>}
        </section>
      </div>
      <p className="forecast-summary__note">
        勝率・3着以内率と期待回収率は別モデルの推定です。100%は配当比較の表示上の区切りで、購入推奨ではありません。
      </p>
      {validPrediction && validRoi && <p className="forecast-summary__note" data-testid="forecast-summary-timing">
        確率計算: {formatJstDateTime(validPrediction.run?.computed_at) ?? PLACEHOLDER}
        {" ／ 期待回収率計算: "}{formatJstDateTime(validRoi.computed_at) ?? PLACEHOLDER}
        {differentTimes && "。計算時刻またはオッズ時点が異なるため、同時点の評価ではありません。"}
        {!timesKnown && "。計算時刻・オッズ時点がそろっているか確認できません。"}
        {validRoi.odds_changed_after_compute && " 計算後に単勝オッズが更新されています。"}
      </p>}
    </section>
  );
}
