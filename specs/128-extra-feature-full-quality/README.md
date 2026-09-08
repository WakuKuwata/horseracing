# 128 追加特徴の全品質研究：進級候補なし

127の固定3候補はいずれも進級せず、128は **NO_ADVANCING_CANDIDATES** で完了した。追加本学習 **0**、smoke学習 **0**、全期間の候補比較 **0**。全成果物can_adopt=false / eligible_for_verdict=false。

| 127の候補 | 2018 winner NLL差 | top3 logloss差 | 元の進級判定 |
|---|---:|---:|---|
| f04 | +0.004013684 | +0.000547785 | QUALITY_REVIEW_REQUIRED |
| finish_decomp | +0.002834715 | +0.000061233 | DEFER |
| weight_deviation | +0.000482886 | +0.000127608 | DEFER |

負が改善。F04はNLLの非改善に加えtop3差が許容+0.0005を超えた。finish_decompとweight_deviationは品質条件内だがNLL差が正だった。必要改善幅や新しい足切りを追加せず、127の全結果と両独立監査が揃った後、事前の「ADVANCE全候補だけ」の規則を適用した。

128の独立監査 **PASS**：22,992照合、最大誤差3.04e-18。候補ゼロでも元raw125の2020〜2026年7cacheとreceiptを読み、全23,030レース・eligible22,990・715日、全started馬3head、元116のNLL/ID/日付・全体NLL/ECEと来歴を再検証した。新しい候補の全期間効力を測った監査ではない。

この結果から3候補が全期間で無効とは結論しない。2018の事前screenを通らなかったため、今回の条件付き全期間研究を実施しなかったという記録である。既存の保持構成・本番モデルは変更しない。

[詳しい結果](result-review.md)・[ゼロ件の完了記録](verdict.json)・[独立監査](evidence/independent-review.json)・[実費範囲](evidence/actual-cost.json)・[最終整合性](evidence/final-integrity.json)
