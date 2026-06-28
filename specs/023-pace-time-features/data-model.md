# Data Model: ペース/時計シグナルの特徴量化 (023)

**スキーマ変更なし**。新規テーブル/カラムなし。既存 `race_results` / `race_horses` を read し、特徴量（計算値）を feature matrix に追加するのみ。loader は SELECT を拡張（DB 構造は不変）。

## ソース列（既存・loader 拡張で読む）
| テーブル | 列 | 用途 | 現 loader |
|---|---|---|---|
| race_results | last_3f（上がり3F）| 正規化上がり as-of | ✅ 既読 |
| race_results | finish_time（走破時計）| 正規化時計 as-of | ❌ 追加 |
| race_results | finish_time_diff（着差）| メンバー相対の時計 as-of | ❌ 追加 |
| race_results | corner_orders（通過順位）| 相対位置/差し脚（任意 group）| ❌ 追加 |
| race_horses | running_style（脚質）| 過去脚質分布（任意 group）| ❌ 追加 |
| races | distance/track_type/going/race_class | 正規化の条件・基準 | ✅ 既読 |

## 1. pace_time group（MVP 主対象, model 特徴）
馬ごとの **過去走 as-of** 集計（対象レースより前のみ、同日除外）:
- `rel_last3f_avg` / `rel_last3f_best`: レース内相対化（そのレースの平均上がりとの差）した近走上がりの平均/ベスト。
- `rel_time_avg`: レース内相対化した走破時計の近走平均（or 条件別 z-score 補助）。
- `finish_diff_avg` / `finish_diff_best`: 着差（finish_time_diff）近走平均/ベスト。
- **source**: 過去 race_results、**timing**: 過去結果由来＝出走表前確定、**missing**: Unknown（新馬/履歴なし→null、0 代入禁止）。中止/故障で欠損の過去走は集計から除外。
- **不変条件**: 今走 result-time 非参照、正規化基準は過去のみ（同走馬今走値・同日・未来年を含めない）。

## 2. position_style group（任意, ablation で採否, model 特徴）
- `rel_corner_pos_avg`: 通過順位 / field_size（頭数正規化）の近走平均、最終コーナー相対位置、位置取り変化。
- `style_*`: 過去走の脚質（逃げ/先行/差し/追込）分布・主傾向（今走 running_style は不使用）。
- **missing**: Unknown、欠損走は除外。寄与が ablation で無ければ採用しない。

## 3. registry / version
- `registry.FEATURE_GROUPS` に `pace_time`（+任意 `position_style`）の各列を追加（020 と同形式の FeatureMeta: source/timing/missing）。
- `FEATURE_VERSION` を features-005 → **features-006**。model_versions.feature_version に記録（再現性）。

## 4. 評価成果物（020 再利用 + 拡張）
- `AdoptionReport`（feature_eval）: 平均 LogLoss/Brier/AUC/ECE・per_fold・**strict majority**・worst-fold LogLoss 上限・**条件別（距離帯/芝ダ/going/年/q帯）差分** を追加。
- `AblationReport`: pace_time / position_style group 寄与分離（diagnostic）。
- `MarketEdgeReport`: p−q calibration・edge bucket 実現勝率・q 条件付き LogLoss（市場超過診断、SECONDARY）。

## エンティティ関係
- 1 race → N 出走馬。各馬に pace_time（+position_style）特徴を付与（過去走 as-of）。
- 特徴は win モデル入力のみ。win→joint（009）派生は不変（IV）。
- すべて計算値、DB 書き込みなし、スキーマ不変（head 不変）。
