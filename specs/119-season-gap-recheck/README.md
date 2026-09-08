# 119-season-gap-recheck

研究判断は **SEASON_GAP_RETAINED**。3seedの平均NLLは旧125列＋gapから0.000627510改善し、raw138列との比較でも0.003036063改善した。既存の近年・top2/top3・校正ガードは全6比較PASS。小さな改善を保持する見直し後の基準に従い、CIが0を跨ぐことだけを理由に棄却しない。

- [結果と残余リスク](result-review.md)
- [固定判断と全数値](verdict.json)
- [事前仕様](spec.md) / [実施計画](plan.md) / [実行前レビュー](pre-run-review.md)
- [独立監査](evidence/independent-review.json) / [監査コード](evidence/independent-review.py)
- [実測費用](evidence/actual-cost.json)

本番採用ではなく研究候補の保持判断。can_adopt=false、eligible_for_verdict=false。

最終照合: [source・成果物・本番モデルの読取確認](evidence/final-integrity.json)。
