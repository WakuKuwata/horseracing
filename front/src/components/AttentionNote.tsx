import type { ErrorInfo } from "../api/client";

/**
 * Feature 138: 注目条件の常設注記(FR-011)。`ExpectedReturnNote` の直後に置く。
 *
 * `ExpectedReturnNote` とは別のコンポーネントにする(FR-008) — 既存の注記テストは裸の `/買/` を
 * 禁じており、注目条件の範囲の禁止語(`ATTENTION_SCOPE`)とは当てる範囲が違うため。
 *
 * 文言は spec FR-011 の凍結値で、一字一句この定数から出す。API の一覧の `disclaimer` は
 * 別の文言(価格の違いと最初の計算の文がない)なので、ここでは使わない。
 * 表示しないもの: 利益を約束する表現・購入を勧める表現・損益色・操作要素。
 */
export const ATTENTION_NOTE_TEXT =
  "注目条件は過去データで最も有望だった条件で、検証済みの条件ではありません。" +
  "S1・S2 は結果を見てから見つけた条件で、過去検証の p 値は多重探索を補正していません。" +
  "選定には現在のオッズを使うため、購入時点の価格が違えば結果も変わります。" +
  "注目条件はレースの最初の計算時点で判定し、その後のオッズ変化では付け外ししません。" +
  "的中や利益を保証するものではありません。";

/**
 * `error` / `isLoading` はこのレースの `GET /races/{id}/attention` の状態(省略可)。取得に失敗した
 * レースの出走表にはチップが 1 つも出ず、「該当する馬がいない」レースと見分けがつかないので、
 * 失敗はここで中立の 1 行として言う(015: 読み込み中・空・失敗は別の状態)。`role="alert"` と
 * エラー色は使わない — 期待回収率の注記と同じ扱いで、ページの主内容は出走表のまま。
 */
export function AttentionNote({
  error = null,
  isLoading = false,
}: {
  error?: ErrorInfo | null;
  isLoading?: boolean;
} = {}) {
  return (
    <div className="ev-note attn-note" data-testid="attention-note">
      <p className="ev-note__title">注目条件について</p>
      <p data-testid="attention-note-text">{ATTENTION_NOTE_TEXT}</p>
      {error ? (
        <p data-testid="attention-error">
          注目条件を取得できませんでした(HTTP {error.status} {error.code})
        </p>
      ) : (
        isLoading && <p data-testid="attention-loading">注目条件を読み込み中…</p>
      )}
    </div>
  );
}
