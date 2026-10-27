# 162 実装結果

2026-10-26完了。**保存仕様を確定し、ネットワーク/DB/実推論を使わない試運転を実装した。新規収集・自動実行は開始していない。**

取得から判断/封印まで30分以内、両モデルの全入力・全確率の保存完了が判断以前、全頭集合と使用価格一致、発走前の固定判断枠を、採用前と読み戻し時に検査する。条件を満たさなければペア全体を不採用にする。

入力保存、ready確認、decision、seal、保存確認receiptを分け、`computed_at`を保存完了時計とみなさない。排他的公開・ファイル/ディレクトリfsync・読み戻し・SHA参照で再実行と中断を扱う。独立テストで見つかった「改ざんreadyを採用後に拒否する」不具合は、採用前の全ready/予約検査へ修正し、マーカーが残らないことを確認した。

公式精算は判断から分離した。HTMLの結果/単勝払戻を正規化資料と照合し、同着は各勝馬の公式額を使用する。不足・不整合は両腕とも未精算null。後日訂正はdecisionを変えず連続revisionで追記する。確定オッズは読まず、外れ/返還の推測補完もしない。

## 試運転と検証

合成11対象の全成功/失敗を保存した。

| ケース | 結果 |
|---|---|
| 通常 / 同着 | 判断採用、公式形式の合成払戻で精算成功 |
| 公式払戻不足 | 判断自体は採用、両案とも未精算 |
| 30分超過、予測遅延、未来quote、旧予測full-info、入力不足、集合変更、原文改変、結果出現 | 全8ケース不採用。理由/SHAのみ保持、staging原文を除去 |

**新規76件（独立攻撃16件を含む）＋関連既存92件、計168テスト通過。** 30分/判断窓境界、µs超の丸め拒否、時計逆行、後更新行、全頭/価格/派生特徴の不整合、ready改ざん、モデル/係数pin、無購入、中止/失格、保存中断/復旧、重複/差替え、seal締切超過、払戻不足・同着・訂正、非公式domain・発走前結果、socket/DB/subprocess入口の禁止を検証した。Scoped Ruffとdiff-checkも通過。

独立担当は主contract/store/settlerをimportせず、最終run-04の全SHA・receipt時計・全頭集合・混合確率/EV/買い目・公式HTMLの単勝額による会計を別計算し一致した。合成採用3、不採用8、精算済2、未精算1を確認。

過去9レース/129頭には、全MEV入力、取得原文、ready/sealの過去receiptが残っていないため、完全契約への昇格は0件。疑似receiptで欠落を埋めたり、今回の合成データを比較母数へ加えたりしていない。

## 範囲と次段階

今回は**契約検査と保存/精算の試運転**であり、実取得・実モデル推論・実時計での前向き証拠取得の稼働確認ではない。全成果物に`offline_rehearsal / simulated_clock=true / eligible_for_prospective=false`を固定した。SHAは改ざん整合の検査であり、外部sourceの真正性や敵対的な全証跡再生成を防ぐ署名ではない。

次の整備対象は、この契約へ実取得/実推論を接続する停止中アダプタと実時計・保存先の検証。収集開始・定期運用・本比較は別段階に残している。

- [保存契約](spec.md) / [実行手順](plan.md)
- [CLI](../../scripts/roi_explore/prospective_rehearsal_162/rehearsal.py)
- [最終試運転report](../../artifacts/162-prospective-offline-rehearsal/run-04/report.json) / [manifest](../../artifacts/162-prospective-offline-rehearsal/run-04/manifest.json)
- [独立レビュー](evidence/independent-review.md) / [独立検算JSON](evidence/independent-rehearsal-audit.json)
