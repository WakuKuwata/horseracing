# 120-probability-mixture

研究上の優先構成は **mixed6**。125列＋gapの3seedと138列＋gapの3seed、それぞれの完成済予測を等重み平均する。win/top2/top3を各々平均し、平均後の再校正やHarville再構成は行わない。

- [結果と残余リスク](result-review.md)
- [固定判断と全数値](verdict.json)
- [事前仕様](spec.md) / [実施計画](plan.md) / [実行前レビュー](pre-run-review.md)
- [独立監査](evidence/independent-review.json) / [監査コード](evidence/independent-review.py)
- [実測費用](evidence/actual-cost.json)

本番採用ではなく研究候補の保持判断。can_adopt=false、eligible_for_verdict=false。

最終照合: [source・成果物・本番モデルの読取確認](evidence/final-integrity.json)。
