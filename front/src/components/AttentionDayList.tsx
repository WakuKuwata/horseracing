import { Link } from "react-router-dom";

import type { ErrorInfo } from "../api/client";
import type { AttentionDayItem, AttentionDayResponse } from "../api/types";
import {
  chipLabel,
  chipNowLabel,
  emphasisLevel,
  freshnessLevel,
  PROGRESS_LABEL,
} from "../lib/attention";
import { formatPostTime, PLACEHOLDER } from "../lib/format";
import { venueName } from "../lib/venues";
import { AttentionProgress } from "./AttentionProgress";

/**
 * Feature 138 (T037): その日の注目条件の該当馬(日付ページ・RaceListPage の一覧の後)。
 *
 * 行は API の並び(発走時刻の昇順・発走時刻不明は末尾 → race_id → 馬番)のまま出す。**強調レベル・
 * 回収率・期待回収率での並べ替えはしない。** 該当(チップの条件・段階・副チップ)は各レースの最初の
 * 計算で凍結した判断時点の値で、front は導き直さない。
 *
 * 強調(検証の進み具合)は出走表と同じ規則: API の 3 軸 + 価格鮮度(最新の計算に使ったオッズの
 * 取得時刻 × 発走時刻 × 描画時刻)の最小値で、チップの条件を現在値で満たさない・現在値が無い馬は
 * 最弱。描画時に 1 回評価し、自動では更新しない(再取得で更新)。
 */
export function AttentionDayList({
  day,
  isLoading,
  error,
  now,
}: {
  day: AttentionDayResponse | undefined;
  isLoading: boolean;
  error: ErrorInfo | null;
  /** 描画時刻(テスト用に固定できる)。省略時はこの描画の時刻。 */
  now?: Date | number;
}) {
  const nowMs = now === undefined ? Date.now() : typeof now === "number" ? now : now.getTime();
  return (
    <div className="panel attn-day" data-testid="attention-day-list">
      <h2>注目条件に該当する馬(判断時点)</h2>
      <p className="note">
        各レースの最初の計算で注目条件(S1〜S4)に該当した馬です。並びは発走順で、強調は表示した時点の
        価格鮮度で評価し、自動では更新しません。
      </p>
      <DayBody day={day} isLoading={isLoading} error={error} nowMs={nowMs} />
    </div>
  );
}

function DayBody({
  day,
  isLoading,
  error,
  nowMs,
}: {
  day: AttentionDayResponse | undefined;
  isLoading: boolean;
  error: ErrorInfo | null;
  nowMs: number;
}) {
  // Neutral states: the race board above stays the primary content of the page.
  if (error) {
    return (
      <p className="note" data-testid="attention-day-error">
        注目条件の一覧を取得できませんでした(HTTP {error.status} {error.code})
      </p>
    );
  }
  if (isLoading || !day) {
    return (
      <p className="note" data-testid="attention-day-loading">
        注目条件の一覧を読み込み中…
      </p>
    );
  }
  if (day.items.length === 0) {
    return (
      <p className="state state--empty" data-state="empty" data-testid="attention-day-empty">
        該当なし
      </p>
    );
  }
  return (
    <div className="table-scroll">
      <table className="data-table attn-day__table" aria-label="注目条件に該当する馬(発走順)">
        <thead>
          <tr>
            <th>レース</th>
            <th>発走</th>
            <th className="num">馬番</th>
            <th>馬名</th>
            <th>注目条件</th>
            <th>{PROGRESS_LABEL}</th>
            <th>最新の計算に使ったオッズの取得時刻</th>
            <th>現在値での確認</th>
          </tr>
        </thead>
        <tbody>
          {day.items.map((item) => (
            <DayRow key={`${item.race_id}:${item.horse_id}`} item={item} nowMs={nowMs} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DayRow({ item, nowMs }: { item: AttentionDayItem; nowMs: number }) {
  const freshness = freshnessLevel(
    item.current_odds_observed_at,
    item.post_time,
    item.has_results,
    nowMs,
  );
  const level = emphasisLevel(item.levels, freshness.level, item.chip_now, item.chip_stage);
  const label = chipLabel(item.chip_rule, item.chip_stage);
  const nowLabel = chipNowLabel(item.chip_now);
  const raceLabel = `${venueName(item.venue_code)} ${item.race_number ?? PLACEHOLDER}R`;
  return (
    <tr
      data-testid={`attention-day-row-${item.race_id}-${item.horse_id}`}
      data-level={level}
    >
      <td>
        <Link to={`/races/${item.race_id}`}>{raceLabel}</Link>
      </td>
      <td data-testid="attention-day-post">
        {item.post_time ? formatPostTime(item.post_time) : "発走時刻不明"}
      </td>
      <td className="num">{item.horse_number ?? PLACEHOLDER}</td>
      <td>{item.horse_name ?? PLACEHOLDER}</td>
      <td>
        <span className={`attn-chip attn-chip--${level}`} role="note" aria-label={label}>
          {label}
        </span>
        {item.chip_s2 && <span className="attn-chip attn-chip--sub">S2</span>}
      </td>
      <td>
        <AttentionProgress level={level} testId="attention-day-progress" />
      </td>
      <td data-testid="attention-day-odds-time">
        {item.current_odds_observed_at
          ? `${formatPostTime(item.current_odds_observed_at)}(価格鮮度 ${freshness.label})`
          : "最新の計算なし"}
      </td>
      <td data-testid="attention-day-now">
        {nowLabel ? <span className="attn-now">{nowLabel}</span> : PLACEHOLDER}
      </td>
    </tr>
  );
}
