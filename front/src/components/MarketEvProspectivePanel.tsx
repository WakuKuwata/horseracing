import { useMarketEvProspective } from "../api/queries";
import type { ProspectiveColumn, ProspectiveDay, ProspectiveFunnel } from "../api/types";
import { formatCi, formatCount, formatP, formatRoi, RoiValue } from "./AttentionRoi";
import { QueryStateView } from "./StateView";

/**
 * B8 (2026-10-28): 期待回収率の列(1.2 の目印と市場混合 1.0 の目印)が、発走前オッズで計算され結果の
 * 前に保存された計算だけを集めたとき、公式の単勝払戻でいくら戻っているかの記録。
 *
 * 規律: 値は API の転記のみ(件数・回収率・区間は読み取り時に API が計算)。並べ替え・色分け・買いを
 * 勧める語は置かない。採否の規則は登録していないので「判断保留」より先の言葉は出さない(300 点に
 * 達しても判定はしない)。回収率の数値は `RoiValue` の単一経路で基準〔公式払戻〕を同じ要素に添える。
 */

export const COLUMN_LABELS: Record<ProspectiveColumn["column"], string> = {
  expected_return: "期待回収率(120%超の馬)",
  expected_return_mix: "期待回収率(市場混合・100%超の馬)",
};

/** Exclusive funnel classes in the API's evaluation order (first failing rule wins). */
export const FUNNEL_ORDER = [
  "jump_race",
  "not_quote",
  "result_known_at_compute",
  "not_single_snapshot",
  "post_time_unknown",
  "observed_after_compute",
  "computed_after_post",
  "field_mismatch",
  "invalid_values",
  "pending_result",
  "payout_race_missing",
  "payout_inconsistent",
] as const;

export const FUNNEL_LABELS: Record<(typeof FUNNEL_ORDER)[number], string> = {
  jump_race: "障害レース(対象外)",
  not_quote: "発走前オッズの取得元が確認できない計算(代替の時刻)",
  result_known_at_compute: "結果取込後の計算",
  not_single_snapshot: "全頭が同じ計算・同じオッズ観測でない",
  post_time_unknown: "発走時刻が不明",
  observed_after_compute: "オッズの観測が計算より後",
  computed_after_post: "発走後の計算(発走前の計算が後の再計算で上書きされた)",
  field_mismatch: "出走馬が計算時と異なる",
  invalid_values: "値が範囲外",
  pending_result: "精算待ち(結果未取込)",
  payout_race_missing: "公式払戻が未保存",
  payout_inconsistent: "公式払戻と結果が不整合",
};

function formatYen(n: number): string {
  return `${formatCount(n)} 円`;
}

function statusText(c: ProspectiveColumn): string {
  if (c.remaining_to_min > 0) {
    return `判断保留(${formatCount(c.min_bets)} 点まであと ${formatCount(c.remaining_to_min)} 点)`;
  }
  return `${formatCount(c.min_bets)} 点に到達(採否の規則は登録していないので、ここでは判定しません)`;
}

function ColumnCard({ c }: { c: ProspectiveColumn }) {
  const ci = formatCi(c.ci);
  return (
    <section
      className="panel"
      aria-label={COLUMN_LABELS[c.column]}
      data-testid={`prospective-column-${c.column}`}
    >
      <h3>{COLUMN_LABELS[c.column]}</h3>
      <dl className="race-meta">
        <div>
          <dt>集計開始日</dt>
          <dd>{c.start_date}</dd>
        </div>
        <div>
          <dt>対象レース</dt>
          <dd>
            {formatCount(c.n_races)}
            {c.n_races_mix_unavailable > 0
              ? `(うち市場混合を計算できないレース ${formatCount(c.n_races_mix_unavailable)})`
              : ""}
          </dd>
        </div>
        <div>
          <dt>購入点数(100 円/点)</dt>
          <dd>
            {formatCount(c.n_bets)} 点・{formatCount(c.n_races_with_bets)} レース・的中{" "}
            {formatCount(c.n_hits)}
          </dd>
        </div>
        <div>
          <dt>投入 / 払戻</dt>
          <dd>
            {formatYen(c.stake_yen)} / {formatYen(c.return_yen)}
          </dd>
        </div>
        <div>
          <dt>回収率</dt>
          <dd data-testid={`prospective-roi-${c.column}`}>
            <RoiValue basis="official_win_payout">
              {formatRoi(c.recovery_rate)}
              {ci ? `(${ci})` : null}
            </RoiValue>
            {c.recovery_rate !== null && c.p_one_sided !== null
              ? ` ・${formatP(c.p_one_sided)}`
              : null}
            {c.recovery_rate !== null && !ci
              ? "・区間なし(開催日が 1 日しかありません)"
              : null}
          </dd>
        </div>
        <div>
          <dt>状態</dt>
          <dd data-testid={`prospective-status-${c.column}`}>{statusText(c)}</dd>
        </div>
      </dl>
    </section>
  );
}

function FunnelTable({ funnel }: { funnel: ProspectiveFunnel }) {
  return (
    <table className="data-table" data-testid="prospective-funnel" aria-label="対象の絞り込み">
      <thead>
        <tr>
          <th>分類(先に当てはまった理由に 1 回だけ数えます)</th>
          <th className="num">レース数</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>期待回収率の保存がある {funnel.since} 以降のレース</td>
          <td className="num">{formatCount(funnel.n_candidate_races)}</td>
        </tr>
        {FUNNEL_ORDER.map((k) => (
          <tr key={k} data-testid={`prospective-funnel-${k}`}>
            <td>対象外: {FUNNEL_LABELS[k]}</td>
            <td className="num">{formatCount(funnel[k])}</td>
          </tr>
        ))}
        <tr data-testid="prospective-funnel-eligible">
          <td>
            <strong>集計対象</strong>
          </td>
          <td className="num">
            <strong>{formatCount(funnel.eligible)}</strong>
          </td>
        </tr>
      </tbody>
    </table>
  );
}

function DayTable({ days }: { days: ProspectiveDay[] }) {
  if (days.length === 0) {
    return (
      <p className="state state--empty" data-state="empty" data-testid="prospective-days-empty">
        まだ記録がありません
      </p>
    );
  }
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="prospective-days" aria-label="開催日ごとの記録">
        <thead>
          <tr>
            <th>開催日</th>
            <th className="num">集計対象</th>
            <th className="num">精算待ち</th>
            <th className="num">期待回収率: 点数</th>
            <th className="num">的中</th>
            <th className="num">払戻(円)</th>
            <th className="num">市場混合: 点数</th>
            <th className="num">的中</th>
            <th className="num">払戻(円)</th>
          </tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d.race_date} data-testid={`prospective-day-${d.race_date}`}>
              <td>{d.race_date}</td>
              <td className="num">{formatCount(d.n_eligible)}</td>
              <td className="num">{formatCount(d.n_pending_result)}</td>
              <td className="num">{formatCount(d.expected_return.n_bets)}</td>
              <td className="num">{formatCount(d.expected_return.n_hits)}</td>
              <td className="num">{formatCount(d.expected_return.return_yen)}</td>
              <td className="num">{formatCount(d.expected_return_mix.n_bets)}</td>
              <td className="num">{formatCount(d.expected_return_mix.n_hits)}</td>
              <td className="num">{formatCount(d.expected_return_mix.return_yen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MarketEvProspectivePanel() {
  const query = useMarketEvProspective();
  return (
    <div className="panel" data-testid="market-ev-prospective">
      <h2>期待回収率の前向き現況(発走前オッズ・公式払戻)</h2>
      <p className="note" data-testid="prospective-definition">
        出走表の「期待回収率」と「期待回収率(市場混合)」の値のうち、発走前の単勝オッズ(取得元を確認できる
        もの)で計算され、結果が出る前に保存された計算だけを集めた記録です。各列の目印(120%超・100%超)の
        馬すべてに 100 円ずつ買ったとして、公式の単勝払戻で精算しています(上限や並べ替えはありません)。
      </p>
      <p className="note" data-testid="prospective-purpose">
        買い目の提案ではなく、表示している値がどれだけ当たっているかを記録するためのページです。
        採否の規則は登録していないので、ここから買う・買わないの結論は出しません。値は推定で、実績では
        ありません。
      </p>
      <p className="note" data-testid="prospective-limit">
        保存表は各レースの最新の計算しか持たないため、発走後に再計算されたレースは発走前の計算が残らず
        対象外になります(下の表の「発走後の計算」がそれです)。したがって、ここに残るのは発走前の計算が
        上書きされなかったレースだけで、その日のレースすべてではありません。計算時刻は保存された値で、
        画面に出た時刻そのものではありません。
      </p>
      <QueryStateView
        isLoading={query.isLoading}
        error={query.error ?? null}
        data={query.data}
        loadingLabel="前向き現況を読み込み中…"
      >
        {(d) => (
          <>
            <p className="note" data-testid="prospective-meta">
              モデル {d.model_version}・取得元の目印 {d.quote_token}・1 点 {d.stake_yen_per_bet} 円・
              精算は公式の単勝払戻・最新の集計対象 {d.last_eligible_race_date ?? "—"}
              ・区間は開催日ブロックの bootstrap({formatCount(d.columns[0]?.bootstrap.b ?? 0)} 回)
            </p>
            <div className="prospective-columns">
              {d.columns.map((c) => (
                <ColumnCard key={c.column} c={c} />
              ))}
            </div>
            <h3>対象の絞り込み</h3>
            <FunnelTable funnel={d.funnel} />
            <h3>開催日ごとの記録</h3>
            <DayTable days={d.by_day} />
          </>
        )}
      </QueryStateView>
    </div>
  );
}
