# 126 結果レビュー

colsample_07を研究候補として保持する。必要改善幅を戻さず、観測された小幅な負のNLL差と全品質条件を認める一方、total CIが0を跨ぐ不確実性をRETAIN_UNCERTAINとして残す。従来の診断用gate_readoutはNO_DECISIONで、本番採否は変更していない。

## 主要品質

| 指標 | 保存値 | 判定 |
|---|---:|---|
| winner NLL差 | -0.000242606 | 観測改善 |
| top2 logloss差 | -0.000151076 | PASS |
| top3 logloss差 | -0.000129753 | PASS |
| 候補ECE | 0.001591140 | PASS |
| 基準ECE | 0.001454482 | — |
| ECE差 | +0.000136657 | +0.001以内 |

直近3年NLL差-0.000766120（eligible10,390・324日）、直近5年-0.000109152（eligible17,294・538日）は既存の害検出CI条件でPASS。canonical・nk・recent_year_onlyも全てPASS。canonicalの馬単位logloss点差は+0.000005029、改善とはいえないが許容幅内である。

非criticalの2026 field_has_nkはNLL差−0.002490292でもCI上限+0.007103213、INCONCLUSIVE_LOW_PRECISION。critical群PASSを、全ての部分集団の改善・非劣性の証明に読み替えない。馬単位subgroupは凍結contractどおり欠けた結果のあるレースも含むall-started母集団で、主要winner/top/ECEの完全結果母集団とは異なる。

## 固定117の2026年診断

| 固定117群 | レース / 開催日 | 候補−raw125 | 候補−raw138 |
|---|---:|---:|---:|
| 2026年全体 | 2,296 / 70 | -0.003549299 | +0.001269536 |
| 2026年中山 | 300 / 25 | -0.003706350 | +0.025751320 |
| 2026年・相対特徴の一部欠測群 | 1,538 / 70 | -0.007222086 | +0.004029638 |

127以降の新しい群や閾値を作らず、元117の `year==2026`、`venue=='06'`、`relative_coverage=='(.5,1)'` をそのまま使用。partialは相対特徴の有効率が0.5超1未満の群であり、レース結果の不完全性を表すラベルではない。

raw138との差は各レースで `126差 + 117.pruning_42` として再構成した保存損失の恒等式で、対象ID/日付・順序を全22,990件照合した。異なる候補の推定改善幅を加算したものではない。どの3群もraw125より良いが、raw138より悪い点差が残り、中山は+0.025751320。既存2026の弱点が解消したとは結論しない。群診断には新CI・新しい採否条件を付けていない。

## 実測費用と検証範囲

| 評価年 | OOF込みfit秒 | worker peak RSS GiB |
|---|---:|---:|
| 2020 | 700.207 | 6.206 |
| 2021 | 753.462 | 5.123 |
| 2022 | 790.806 | 6.294 |
| 2023 | 789.954 | 8.480 |
| 2024 | 893.889 | 6.195 |
| 2025 | 953.281 | 6.615 |
| 2026 | 993.833 | 6.359 |

計5875.432秒（97.924分）。7 outerは各7 inner OOF＋finalで計56 booster。基準再学習0。smokeは5trees/2OOFのbaselineとcandidate各1 outer、計4 boosterを別計上し、両者合計の新boosterは60。最大2 workers・各1 thread。worker RSS最大は8.480GiBだが、同時総メモリとは異なる。

保存fitタイマーはfactory.fit開始から校正・学習列/params検査・予測作成までを含み、cache metadata構築中に終了する。行列読込、prepare、smoke、計時後のpayload/dtype検査・cache書込、receipt検証、評価・bootstrap、summary、独立監査、文書化を含まない。累積fit秒をwall timeと表現せず、評価全工程/研究全体wall timeは未計測として記録した。

独立数値監査は69,029照合・最大誤差5.55e-17でPASS。methodは実行前固定SHA `59babf4b501dc9ec6dc1a31e43a40c8ee9bfcaac77b25488432f9ed4e3f94582`、結果JSON `cfdd613c074ce6440e88c8a08ec8ec5f060842d0c23a222260c9e41cfee53aae`。追加学習・追加校正fitは0。監査後のsource/config/runtime・全7receipt/report/evidenceを再検証し、別途final-integrityに固定117証拠と資料SHAを保存した。

この結果はseed42のrawモデル比較。別seed、現行joint補正やmixed6への増分、新しい未使用holdoutは未確認であり、それらを確立した結果とはしない。
