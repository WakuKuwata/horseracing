/**
 * モデル勝率と市場評価、荒れ度分類の実測上の位置づけをレース単位で常時開示する。
 *
 * 数値の出所: feature 047 / 2021 年以降 / n=181,341。
 * feature 番号は内部の検証管理番号なので画面には出さず、誤った検証への帰属を防ぐため
 * コード上のコメントにだけ固定する。
 */
export function ModelMarketStanding({
  canonicalConsistent,
}: {
  canonicalConsistent: boolean | null | undefined;
}) {
  // null はオッズがなく市場との比較が成立しないため、明示的に true の場合だけ表示する。
  if (canonicalConsistent !== true) return null;

  return (
    <p className="table-hint" data-testid="model-market-standing">
      <span data-testid="win-model-standing">
        勝率予測では市場評価がモデルを上回っています(検証データ 181,341 件・すべての検証セグメント)。
      </span>
      <br />
      <span data-testid="chaos-classification-standing">
        一方、荒れ度の分類には実測上の識別力があります。
      </span>
      <br />
      <span>
        モデル勝率との差を利益機会とは解釈せず、レース傾向と見送り判断の補助情報としてご覧ください。
      </span>
    </p>
  );
}
