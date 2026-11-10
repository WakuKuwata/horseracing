import type { ErrorInfo } from "../api/client";
import type { MarketEvResponse, MarketEvUnavailable } from "../api/types";
import { formatJstDateTime, PLACEHOLDER } from "../lib/format";

/**
 * Feature 137: 期待回収率の注記。出走表の直前に常時表示する。
 *
 * 期待回収率は「モデル勝率」の列とは**別の市場連動モデル**(現在の単勝オッズを入力に持つ)の
 * 推定なので、それを最初の一文で言い切る。過去検証の数値は凍結値の転記であり、画面側で計算しない。
 * 120% 超でも利益は確認できていない — これを隠さないことがこの注記の目的である。
 *
 * 2026-11-10: 表示版を `mev-ens15-v1` → `mev-ens15-jrdb-v5`(JRDB で学習・同じ精度)に切り替え、検証要約・混合の文言を
 * 再凍結値(specs/138 evidence/rules_S1_S5_freeze.json・market_mix_20261110_jrdb_v5)に置き換えた。
 *
 * Feature 138(T038): 列の値は 15 seed 平均(`mev-ens15-v1`)に切り替わった。検証要約は 138 の
 * 再凍結値(spec の S3 = 15 seed 平均 >120% の通算・確認窓、全馬は `all_horses.all`)に置き換え、
 * 単 seed 表示との系列の違いを明記する。137 の「120%超の目印」は注目条件チップに統合したので、
 * その文は削除した。
 *
 * 表示しないもの: 利益を約束する表現・買いを勧める表現・損益色・操作要素。
 */

/** 過去検証の要約(固定文言・spec 138 の再凍結値=S3 の通算/確認窓と全馬から転記)。 */
export const EXPECTED_RETURN_VALIDATION =
  "過去検証(2010〜2026 年、各年を前年までのデータで学習): 期待回収率 120% 超の馬の単勝回収率は 101.1%(95% 区間 94〜108%)、2019 年以降は 105.0%。参考として全馬は 72%。120% を超えても利益は確認できていません。";

/** 表示系列の切り替え(spec 138 FR-007)。 */
export const EXPECTED_RETURN_SERIES_CHANGE =
  "2026 年 11 月 11 日から、JRDB のデータで学習したモデル(15 seed 平均)に切り替えました。精度は以前のモデルと同じ程度ですが、選ばれる馬は一部変わるため、それ以前の表示や注目条件の記録とは比較できません(2026 年 10 月の単 seed からの切替も同様です)。";

/** 直近の不確かさ(spec 137 SC-004・憲法 III の開示)。 */
export const EXPECTED_RETURN_UNCERTAINTY =
  "2025〜26 年は保存オッズに発走前の値が混ざるため検証の精度が落ちます。発走前オッズでの前向きの検証はまだ行っていません。";

export const EXPECTED_RETURN_DISCLAIMER = "的中や利益を保証するものではありません。";

/** Feature 141: 市場混合(並走表示)の固定文言。数値は fit_mix.py / heldout.py の凍結値の転記。 */
export const MIX_DEFINITION =
  "『期待回収率(市場混合)』は、期待回収率の推定勝率を市場の評価(単勝オッズの支持率)と混ぜ直して計算し直した期待回収率です。混ぜ方の係数は各年、前年までのデータで決めています。期待回収率が高く出た馬ほど過大になりやすい分を抑えます。期待回収率の列と注目条件はこれまでどおりです。";

export const MIX_VALIDATION =
  "過去検証(2013〜2026 年 6 月・確定オッズ・各年を前年までのデータで決定): 市場混合で 100% を超えた馬(23,529 頭)では、推定に対する実際の勝ち数の比が 0.97 でした(混ぜる前は 120% 超の馬で 0.74)。120% を超える馬ではなお推定が高すぎる(比 0.83)ため、表の「100%超」は 100% を境にしています。この境は以前のモデルの検証の結果を見て決めました。";

export const MIX_HELDOUT =
  "以前のモデルでは、検証に使っていない期間(2026 年 8 月 10 日〜10 月 18 日)で勝ち馬の当てはまりの悪化は見られませんでした。2026 年 11 月に切り替えた今のモデルの混ぜる比率は同じ手順で決め直したもので、この確認はしていません(使っていない期間が残っていないため)。";

export const MIX_LIMIT =
  "混ぜる比率は確定オッズで決めたもので、発走前のオッズで表示した値の検証はこれから記録します。";

const MIX_UNAVAILABLE_TEXT: Record<string, string> = {
  no_coefficients: "このレースの年の混ぜる比率がまだ決まっていないため、市場混合は表示していません",
  invalid_inputs: "このレースは計算に必要な値がそろわないため、市場混合は表示していません",
  model_mismatch: "このモデルの値には市場混合を計算していません",
  disabled: "市場混合の表示は停止中です",
};

function definition(modelVersion: string): string {
  return (
    "期待回収率は、出走表の勝率とは別の市場連動モデル" +
    `(${modelVersion})が、単勝オッズと各馬の過去成績から推定した勝率 × 単勝オッズです。`
  );
}

const UNAVAILABLE_TEXT: Record<MarketEvUnavailable["reason"], string> = {
  not_computed: "このレースの期待回収率はまだ計算されていません",
  field_changed: "出走馬が変わったため再計算待ちです",
  odds_unavailable: "単勝オッズがそろっていないため表示していません",
};

export function ExpectedReturnNote({
  marketEv,
  isLoading,
  error,
}: {
  marketEv: MarketEvResponse | undefined;
  isLoading: boolean;
  error: ErrorInfo | null;
}) {
  return (
    <div className="ev-note" data-testid="expected-return-note">
      <p className="ev-note__title">期待回収率について</p>
      <NoteBody marketEv={marketEv} isLoading={isLoading} error={error} />
    </div>
  );
}

function NoteBody({
  marketEv,
  isLoading,
  error,
}: {
  marketEv: MarketEvResponse | undefined;
  isLoading: boolean;
  error: ErrorInfo | null;
}) {
  if (isLoading) {
    return <p data-testid="expected-return-loading">期待回収率を読み込み中…</p>;
  }
  // Neutral: no alert role / error colour — the rest of the page stays the primary content.
  if (error) {
    return (
      <p data-testid="expected-return-error">
        期待回収率を取得できませんでした(HTTP {error.status} {error.code})
      </p>
    );
  }
  if (!marketEv) {
    return <p data-testid="expected-return-loading">期待回収率を読み込み中…</p>;
  }
  if (marketEv.status === "unavailable") {
    return <p data-testid="expected-return-unavailable">{UNAVAILABLE_TEXT[marketEv.reason]}</p>;
  }

  const observedAt = formatJstDateTime(marketEv.odds_observed_at) ?? PLACEHOLDER;
  const computedAt = formatJstDateTime(marketEv.computed_at) ?? PLACEHOLDER;
  return (
    <>
      <p data-testid="expected-return-definition">{definition(marketEv.model_version)}</p>
      <p data-testid="expected-return-times">
        オッズ取得 {observedAt} ・計算 {computedAt}
      </p>
      {marketEv.odds_changed_after_compute && (
        <p data-testid="expected-return-odds-changed">
          計算後に単勝オッズが変わっています(表示は計算時のオッズ基準)
        </p>
      )}
      {!marketEv.result_pending_at_compute && (
        <p data-testid="expected-return-post-result">
          結果確定後のオッズで計算した事後の参考値です
        </p>
      )}
      <p data-testid="expected-return-validation">{EXPECTED_RETURN_VALIDATION}</p>
      <p data-testid="expected-return-series">{EXPECTED_RETURN_SERIES_CHANGE}</p>
      <p data-testid="expected-return-uncertainty">{EXPECTED_RETURN_UNCERTAINTY}</p>
      <p className="ev-note__subtitle">市場混合について</p>
      <p data-testid="expected-return-mix-definition">{MIX_DEFINITION}</p>
      {marketEv.mix.status === "unavailable" && (
        <p data-testid="expected-return-mix-unavailable">
          {MIX_UNAVAILABLE_TEXT[marketEv.mix.reason] ?? MIX_UNAVAILABLE_TEXT.invalid_inputs}
        </p>
      )}
      <p data-testid="expected-return-mix-validation">{MIX_VALIDATION}</p>
      <p data-testid="expected-return-mix-heldout">{MIX_HELDOUT}</p>
      <p data-testid="expected-return-mix-limit">{MIX_LIMIT}</p>
      <p data-testid="expected-return-disclaimer">{EXPECTED_RETURN_DISCLAIMER}</p>
    </>
  );
}
