# 122 市場特徴・列サンプリングの一次比較結果

2018年の同じレースとraw125列・seed42の基準予測で3案を比較した。**colsample_bytree=0.7だけを全期間の研究126へ進める。** 小さな負の差も残し、信頼区間が0を跨ぐことは一次進級の否決条件にしない。

差はcandidate−baseline。NLLは小さい方がよい。

| 候補 | NLL | NLL差 | top2損失差 | top3損失差 | 判定 |
|---|---:|---:|---:|---:|---|
| 基準raw125 | 2.022998463 | — | — | — | 比較基準 |
| 市場順位4列をpercentile等5列に置換（F03） | 2.026317312 | +0.003318850 | +0.000227206 | −0.000071643 | DEFER |
| 条件別市場支持6列を追加（F05） | 2.027720404 | +0.004721941 | +0.000503933 | +0.000683339 | QUALITY_REVIEW_REQUIRED |
| 各木で使う列を70%へ | 2.022298209 | −0.000700254 | −0.000237139 | +0.000017811 | ADVANCE_TO_FULL_RESEARCH |

F05はNLLの悪化に加え、top2/top3の許容差+0.0005を超えた。F03もNLLが悪化した。列70%の総CIは[−0.008554362, +0.007121582]で、改善の不確実性は大きい。ここでの通過は全期間での保持や本番採用を意味しない。

母集団は2018-01-01〜2018-12-31の全3454レース、winner NLL3448レース・109開催日。全案seed42、900trees、8 OOF、その他の学習・校正・マスク条件を統一した。基準は来歴を検証した旧native cacheを再利用し、新規本学習は3 outer jobs（24 booster fits）。構造確認の5trees/2 OOFによる4案8 booster fitsは別計上する。

独立監査では予測・ラベル・全3head損失・校正・CI・進級判定の31071確認がPASS（最大差4.441e−16）。さらに新11列を元Framesからscalar計算し、958011行・10538121セルが一致した（最大差1.776e−15）。同日・将来情報の除外も確認した。

- [事前仕様](spec.md) / [固定設定](gate-config.json) / [実行前レビュー](pre-run-review.md)
- [集計結果](verdict.json) / [解釈と制限](result-review.md)
- [予測の独立監査](evidence/independent-review.json) / [特徴量の独立監査](evidence/independent-feature-review.json)
- [実測コスト](evidence/actual-cost.json) / [最終整合確認](evidence/final-integrity.json)
- [後続126](../126-market-feature-full-quality/spec.md)

過去の開発用期間を用いた研究で、can_adopt=false / eligible_for_verdict=false。本番切替・年間確認枠の予約は行っていない。gap補正やモデル平均への上積みとして今回の差を加算しない。
