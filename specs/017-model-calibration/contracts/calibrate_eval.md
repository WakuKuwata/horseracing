# Contract: モデル確率校正の学習・評価（CLI / probability）

walk-forward でモデル p の校正器を学習し、生 p vs 校正 p' の校正品質を評価する（US1 採用ゲート）。

## コマンド（例）

```
probability calibrate-eval --from <date> --to <date> [--method power|beta|isotonic] \
  [--select mle] [--min-races 50] [--min-wins 30] [--bands popularity] [--model-version <mv>]
```

## 入力（採点のみ結果使用）

- `race_predictions.win_prob`（モデル p、対象 prediction_run）。
- `race_results`（realized 1 着、採点のみ）。同着は教師から除外。

## 処理

1. 期間レースを race_id 順に walk-forward。各 fold で **対象レース開始より厳密前**(race_before: (date,race_id))
   のサンプルのみで校正器を fit。方式・ハイパラ選択も窓内（選択リーク禁止）。
2. fit: power は `p'∝p^γ`、γ を normalized winner-NLL の golden-section MLE（決定論）。窓不足 →
   temperature のみ / identity(γ=1) fallback。
3. apply: p' をレース内正規化 + engine-consistent clip（009 入力と一致）。
4. eval: 生 p / 校正 p' の NLL・Brier・ECE・reliability（overall + 人気帯別 over/under・slope・
   calibration-in-the-large）。**009 後の券種別 reliability**（exacta/trifecta winner NLL/Brier）も before/after。

## 出力（PCalibrationReport + joint reliability）

scope 別の nll/brier/ece、reliability ビン、overconfidence 指標、improved フラグ、同着除外件数、
009 後 joint 非悪化判定。**採用ゲート = NLL/Brier 改善（主）+ ECE/reliability（補助）+ joint 非悪化（必須）**。

## 不変条件

- 校正器は対象レース結果を読まない。選択リーク 0。決定論（同一入力 → 同一校正器・指標）。
- p' は Σ=1 の race-normalized 形で評価・適用（評価 == 使用ベクトル）。p≠q（q 側に触れない）。
