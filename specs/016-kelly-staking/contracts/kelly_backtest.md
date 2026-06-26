# Contract: bankroll backtest（CLI）

期間指定で Kelly stake の bankroll 推移を flat（011/012）と同一条件比較。レポートを返す（非永続）。

## コマンド（例）

```
betting kelly-backtest --from <race_id|date> --to <race_id|date> \
  [--bankroll 100] [--ruin-threshold 0.0] [--lambda-real 0.25] [--lambda-est 0.10] \
  [--cap-bet 0.05] [--cap-total 0.10] [--o-min 1.5] [--allocation exact|heuristic] \
  [--bootstrap-blocks 50] [--bet-types ...] [--compare flat]
```

## 入力（採点のみ結果使用）

- 各レースの Kelly/flat 買い目（kelly_recommend と同一ロジック、結果非参照で生成）。
- `race_results`（採点のみ）、`exotic_odds`（実 payout）/ 010 推定（二重疑似 payout）。

## 処理

1. walk-forward（時系列順）に各レースを処理: 買い目生成 → 結果で的中判定（011 の券種別: 順序/無順序/包含、
   複勝・ワイドは複数当たりをベット単位、009 field-size 規則）→ bankroll 更新。
2. 払戻 = 実 exotic オッズ present なら実、無ければ O_est（二重疑似）。的中 +stake·(O−1)、外れ −stake、同着按分。
3. bankroll が ruin 閾値割れ → 当該経路停止（ruin 記録）。
4. 実区間 / 二重疑似区間を分離集計。
5. 破産確率 = 実経路 ruin（0/1）+ block bootstrap（時系列ブロック保持リサンプリング）での ruin 割合。
6. flat と**同一条件**（同一買い目母集団・同一オッズ源・同一期間）で比較。

## 出力（BankrollBacktestResult レポート）

終端 bankroll / 対数成長率 / 最大DD / 破産確率 / 分散 / 最大連敗 / 件数・的中率・見送り率、
strategy(kelly|flat) × segment(real|double_pseudo) で分離、baseline 比較と success 判定。

## 不変条件 / 成功条件

- success = flat に対し**リスク調整後成長で優位**（対数成長率↑ かつ 最大DD・破産確率が許容内）。**単なる ROI>1 は不可**。
- 二重疑似 ROI と実 ROI を合算しない（分離明示）。
- 順序を壊す i.i.d. シャッフルを破産確率推定に使わない（walk-forward + block bootstrap）。
- 買い目生成は結果非参照、採点のみ結果使用（リーク境界）。決定論（bootstrap は固定ブロック化で再現可能）。
