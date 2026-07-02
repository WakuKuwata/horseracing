# Data Model: コーナー通過順の軌跡特徴 (041)

**スキーマ変更なし**(migration head 不変)。既存テーブルの読み取りのみ。

## 入力(既存、loader 済み・fingerprint 包含)

| ソース | 列 | 用途 |
|---|---|---|
| race_results | corner_orders(数値文字列 list)・finish_order・result_status | 過去走の軌跡生スコア(finished のみ) |
| race_horses | entry_status | started 判定・field_size(過去走の started 数) |
| races | race_date | as-of 境界(merge_asof キー) |

## 中間表現(runs、非永続)

| 列 | 定義 | エッジ |
|---|---|---|
| corner_first / corner_last | parse(corner_orders) の先頭/末尾 | parse 失敗/空 → NaN |
| late_gain | (corner_last − finish_order)/field_size | field_size≤0 → NaN |
| early_pos | corner_first/field_size | 〃 |
| mid_move | max(pos[j]−pos[j+1])/field_size | コーナー<2 → NaN |

## 出力特徴(4 列、registry group=corner_trajectory)

| 列 | 集約 | dtype | missing |
|---|---|---|---|
| asof_late_gain_avg | 過去走 expanding 平均 | float64 | NULL(NaN) |
| asof_late_gain_best | 過去走 expanding 最大 | float64 | NULL |
| asof_early_pos_avg | 過去走 expanding 平均 | float64 | NULL |
| asof_mid_move_avg | 過去走 expanding 平均 | float64 | NULL |

- timing=PRE_ENTRY(過去走由来、レース前に確定)・source="pace"(023 と同区分)。
- STATIC_COLUMNS 非収録 → materialized_columns 自動収録(025)。
- FEATURE_VERSION: features-011 → **features-012**。

## 不変条件

- **INV-C**: 値の正しさ(late_gain/early_pos/mid_move の式・expanding 集約)。
- **INV-L**: 今走 corner/finish 変更・同日変更・未来変更で対象行不変。ソース grep で今走列の生参照なし(merge_asof 経由のみ)。
- **INV-P**: materialize == in-memory bit 一致(4 列込み)。source_fingerprint 無改修。
- **INV-N**: 過去走なし/有効軌跡なし → NaN(0 と区別、Unknown≠0)。
