# 127 追加特徴3候補の一次比較結果

**全期間研究へ進める候補は0件。** 2018年・raw125列・seed42の同じ基準で、F04残差6列、088着順分解10列、過去体重偏差1列を個別に追加した。3案ともwinner NLLの点推定が悪化し、F04はtop3損失の許容差+0.0005も超えた。

差はcandidate−baseline。NLLは小さい方がよい。

| 候補 | NLL | NLL差 | top2損失差 | top3損失差 | 判定 |
|---|---:|---:|---:|---:|---|
| 基準raw125 | 2.022998463 | — | — | — | 比較基準 |
| 市場期待と結果の残差6列（F04） | 2.027012146 | +0.004013684 | +0.000269509 | +0.000547785 | QUALITY_REVIEW_REQUIRED |
| 着順正規化・ラグ分解10列（088） | 2.025833178 | +0.002834715 | -0.000097661 | +0.000061233 | DEFER |
| 過去体重の個体内偏差1列 | 2.023481349 | +0.000482886 | -0.000023025 | +0.000127608 | DEFER |

必要改善幅は0で、小さな負の差も進級対象だった。今回の見送りは改善幅不足やCI跨ぎによるものではない。一方、単年・単一seedの非改善なので、効果が存在しないことや別期間での悪化が確定したとは言わない。

全3454レース、winner NLL3448レース、109開催日。900trees/8 OOF、mask・その他レシピを統一し、基準は来歴検証済みnative cacheを再利用した。新規本学習は3 outer jobs（24 booster fits）、保存されたfit時間の合計は **1756.146秒（29.27分）**。2並列の経過時間ではない。5trees/2 OOFの構造確認8 booster fitsは別計上する。

独立予測・指標・CI・進級監査は **31071確認PASS**、最大数値差4.441e-16。独立特徴監査は **958,011行×17列=16,286,187セルPASS**、最大差4.414e-13。体重偏差・count・raw lagは差0で、曖昧な体重日やFINISHED/entryの不整合も今回の元データには無かった。

[条件付き次段128](../128-extra-feature-full-quality/spec.md)へ進む候補は0件となるため、全期間の追加fitも0件。今回の差を既保持の補正・モデル平均の効果へ加算しない。本番変更・年間確認枠はなく、can_adopt=false / eligible_for_verdict=false。

- [事前仕様](spec.md) / [固定設定](gate-config.json) / [実行前レビュー](pre-run-review.md)
- [集計結果](verdict.json) / [解釈と限界](result-review.md)
- [予測独立監査](evidence/independent-review.json) / [特徴独立監査](evidence/independent-feature-review.json)
- [実測費用](evidence/actual-cost.json) / [最終整合確認](evidence/final-integrity.json)
