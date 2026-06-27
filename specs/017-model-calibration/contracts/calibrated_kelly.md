# Contract: 校正 + haircut 適用 Kelly と比較（CLI / betting）

校正済み P_model' と edge haircut を 016 の Kelly 推奨・backtest に opt-in 適用し、生 Kelly と比較する
（US2/US3 diagnostic）。

## コマンド（例）

```
betting kelly-recommend <race_id> --p-calibrator power:<gamma>|fit \
  --haircut-type relative|absolute --haircut 0.05 [既存 016 フラグ ...]

betting kelly-calibration-compare --from <date> --to <date> \
  --modes raw,cal,cal+haircut [--haircut-type relative --haircut 0.05] \
  [--p-window <date_from:date_to>] [--q-calibrator power:<gamma>] [--model-version <mv>]
```

## 処理（推奨生成）

1. 011 canonical field を構築 → `apply_p_calibrator(field, p_calibrator)` で p_norm→p'_norm（009 入力一致）。
2. 009 で P_model'。オッズは実(012)優先・無ければ推定(010)。q 校正(013)併用時は **q 校正で O_est 確定 →
   p 校正 P_model' と結合**（p を market 側に戻さない）。
3. Kelly: edge=P_model'·O−1 → **haircut**（relative:(1−h)·edge / absolute:edge−h）→ f*=edge_adj/(O−1) →
   λ·cap・相互排他配分（016）。edge_adj≤0 は見送り。
4. 保存（016 と同一 + logic_version に校正/haircut/窓/base_model_version 追記）。

## 処理（比較）

walk-forward bankroll backtest を **raw / cal / cal+haircut** で同一条件実行（016 の harness 再利用）。
さらに **2×2(raw/cal p × raw/cal q)** で EV・edge 分布・Kelly リスクを算出。

## 出力（KellyCalibrationCompareReport）

mode 別の 6 指標（終端 bankroll・対数成長率・最大DD・破産確率・分散・最大連敗）、risk_not_worse、
over_conservative、2×2 edge 分布、verdict。

## 不変条件 / 成功条件

- success = **校正改善 かつ Kelly リスク非悪化**（成長維持で破産/最大DD 低下）。Kelly 非悪化は必須ガード。
- 過剰保守（cal+haircut で成長を過度に削る）・二重補正（2×2 で edge 過縮小）を検出・明示。
- p'・haircut・edge_adj・fraction は features/training に戻さない（leak-guard）。決定論。
