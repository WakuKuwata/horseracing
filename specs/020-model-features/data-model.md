# Data Model: モデル改善 — 特徴量拡張 (020)

**スキーマ変更なし**。新特徴は計算され `registry.REGISTRY` に登録、feature_version=features-005 を bump、
既存 model_versions / prediction / eval テーブルを使用。新規は value object（評価レポート）と feature 定義。

---

## 1. 永続スキーマ

変更なし。model_versions.feature_version に新版（features-005）を記録。予測は既存 prediction_runs /
race_predictions、評価は既存 eval 経路。migration head = 0006（不変）。

---

## 2. 新規特徴量（registry.FeatureMeta で登録、group 付与）

| 特徴 | group | source | timing | missing | leak 機構 |
|---|---|---|---|---|---|
| `avg_last3_finish` | recent_form | history | pre_entry | NULL | as-of 直近3走（merge_asof backward, exact 無し） |
| `recent_win_rate` | recent_form | history | pre_entry | NULL | daily cumsum−当日（直近窓） |
| `dist_band_win_rate` | aptitude | history | pre_entry | NULL | 距離帯別 as-of（前のみ・同日除外） |
| `dist_band_avg_finish` | aptitude | history | pre_entry | NULL | 同上 |
| `surface_win_rate` | aptitude | history | pre_entry | NULL | 芝ダ別 as-of |
| `field_size` | race_condition | races/race_horses | pre_entry | ZERO_OK | 当該レース出走頭数（結果非依存） |
| `class_transition` | race_condition | history | pre_entry | NULL | 前走 race_class との差（昇/同/降）、前走のみ |
| `jockey_win_rate` | human_form | history | pre_entry | NULL | **jockey_id daily cumsum−当日**（対象行+同日除外）+ walk-forward 前 |
| `trainer_win_rate` | human_form | history | pre_entry | NULL | 同上（trainer_id） |

- 全て Unknown=NULL（過去不在に 0 代入しない）。各特徴に **cutoff テスト**（当日以降変更で不変）+ 跨馬系は
  **target-row 除外テスト**（対象行・同日結果変更で不変）。
- group ラベルは ablation 用（registry 拡張 or 別マップ `FEATURE_GROUPS`）。

---

## 3. AdoptionReport（評価、非永続）— US2 採用ゲート

| フィールド | 意味 |
|---|---|
| baseline_model / candidate_feature_version | 比較対象 |
| per_fold | fold 別 {LogLoss, Brier, AUC, ECE}（new vs baseline）+ diff |
| mean_* / n_folds | 平均指標 |
| n_winning_folds / worst_fold_logloss_diff / worst_fold_ece_diff | fold 別差分（偶然 fold 排除） |
| primary_pass | LogLoss 改善 かつ ECE 非悪化（PRIMARY ゲート） |
| group_ablation | {recent_form/aptitude/race_condition/human_form → 寄与（LogLoss 差）} |
| stability | fold 間の gain/SHAP 符号・順位安定性（除外候補） |
| adopted | primary_pass かつ fold 別差分 OK かつ過学習検査 OK |

---

## 4. SecondaryDiagnostic（非永続）— US3

| フィールド | 意味 |
|---|---|
| pseudo_roi / kelly | 011/016 backtest（高分散・参考） |
| market_edge | p−q calibration / edge bucket 別実現勝率 / q 条件付き LogLoss |
| note | 「絶対校正改善≠市場超過」、成功基準=OOS win 改善 |

---

## 5. 評価＝デプロイ一致（候補固定 + fold 内ハイパラのみ、leak 機構）

候補特徴集合は**事前固定**（既存 + 新規9）。walk-forward 各 fold: 学習窓を inner train/val に分割 → **ハイパラ・
early stopping のみ**を inner で完結（OOS を見て特徴選択しない）→ OOS（fold test）で「固定集合 vs baseline」。
採用時は同一固定集合を全体再学習＝評価モデルとデプロイモデルが一致。group ablation は diagnostic（採用特徴の
選別に使わない）。

---

## 6. 不変条件 / 境界

- リーク境界（II）: 全特徴 as-of/out-of-fold、同日除外、跨馬は対象行除外。選択も fold 内。market odds/結果は
  特徴にしない。
- 確率整合性（IV）: win→joint（009）維持。
- 再現性（V）: feature_version、決定論（seed）。
- 採用（III）: baseline 未超過なら不採用（false positive を出さない）。
- スキーマ変更ゼロ。
