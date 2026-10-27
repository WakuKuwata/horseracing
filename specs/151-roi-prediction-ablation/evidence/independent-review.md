# 151 独立監査

判定: PASS。

- Protocol and all frozen input/source hashes verified; primary remains preweight.
- Complete fields, unique starter IDs and horse numbers, one winner, date/odds contracts verified.
- All annual softmax predictions, coefficient bounds, nested train losses and recorded cutoffs verified.
- All 10 arms: exact tickets/rank/cap/strict thresholds, full-population accounting, overall/yearly quality and risk summaries verified.
- All paired points, 4000 same-day resamples, unadjusted/Bonferroni intervals and outlier sensitivity reproduced.
- Primary preweight decision and historical-only retention limitations verified.

主比較の判定: `incremental_value_not_demonstrated`。

analysis.py / inputs.py をimportせず独立計算。係数の再fitは行わず、年次cutoffと保存係数による全頭予測を確認した。入力来歴と130再構成の確認は別の入力監査と合わせて読む。確定オッズ・既視期間の条件付き結果であり、本番採用や削除の証拠にはしない。

## 保存オッズの診断

採点後の追加診断で、`Σ(1/odds)<1` のレースが12件あった（最小0.575659）。そのため正規化市場だけの参照腕にも112購入が生じる。保存値を同期した実行可能な確定価格とみなせない可能性があり、回収率の絶対値を運用利益の証拠にしない。事前条件にない除外は行っていない。

この12レースにおける主政策の利益はROI系のみ+2,550円、予測追加+2,350円、差−200円であり、全体の追加利益+22,410円を作った原因ではない。この診断は固定した主判定を変更しない。

対象レースと逆数和:

- `202203020810`: 0.900734842
- `202203030311`: 0.946329995
- `202204030802`: 0.869763096
- `202206030406`: 0.954017559
- `202209040801`: 0.708214529
- `202302010202`: 0.968916001
- `202309030706`: 0.881484535
- `202309050107`: 0.955891321
- `202404040303`: 0.983796195
- `202405010304`: 0.695247592
- `202407040403`: 0.575659484
- `202508010810`: 0.904710296
