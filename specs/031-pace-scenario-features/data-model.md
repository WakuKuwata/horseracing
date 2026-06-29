# Data Model: 展開・ペース構成特徴 (031)

スキーマ変更なし(DB migration head 0006 不変・新テーブルなし)。本 feature は features の **特徴量列** を 7 追加するのみ。DB read は 023 と同一(新規読取列なし)。

## 入力（既存・再利用、新規読取なし）

`pace_scenario_features.build_pace_scenario_features(frames)` の唯一の入力は **023 `build_pace_features(frames)` の出力**(per (race_id, horse_id)):

| 列 | 由来 | 意味 |
|---|---|---|
| front_runner_rate | 023, STARTED 過去レースの running_style∈{逃げ,先行} 率(as-of, rolling N=5) | 自馬の先行傾向 |
| closer_rate | 023, running_style∈{差し,追込,ﾏｸﾘ,マクリ} 率(as-of) | 自馬の差し傾向 |
| rel_corner_pos_avg | 023, 過去レースの最終コーナー相対位置(as-of, 0=先頭〜1=最後方) | 自馬の道中位置 |

加えてフィールド母集団判定に `frames.race_horses.entry_status`(STARTED 判定)を使用。**生の今走 running_style/corner_orders/result/finish_order は読まない**。

## 出力（pace_scenario group, 全て float64, missing=NULL）

per (race_id, horse_id)。集計は entry_status==STARTED の馬を race のフィールドとし、自馬を除外(leave-one-out)。null は集計母数から除外。

| 列 | 定義 | NaN 条件 |
|---|---|---|
| `field_front_rate_ex_self` | 同レース他馬(STARTED, self 除外)の front_runner_rate の平均(非 null のみ) | 他馬の非 null が 0 |
| `field_closer_rate_ex_self` | 同上 closer_rate の平均 | 同上 |
| `pace_imbalance_ex_self` | field_front_rate_ex_self − field_closer_rate_ex_self(正=先行多=ハイペース予想=差し有利) | いずれかが NaN |
| `front_pressure` | own.front_runner_rate × field_front_rate_ex_self | own または field が NaN |
| `closer_setup` | own.closer_rate × field_front_rate_ex_self | own または field が NaN |
| `style_mismatch` | own.rel_corner_pos_avg − 同レース他馬(self 除外, 非 null)の rel_corner_pos_avg 平均 | own または他馬集計が NaN |
| `field_style_coverage` | 同レース(STARTED)で front_runner_rate が非 null の馬数 / field_size | field_size=0(理論上のみ) |

- **field_size** = race の STARTED 馬数(023 と同一定義)。
- **leave-one-out 算術**: 非 null の和 S と件数 C を race 単位で集計し、自馬が非 null なら (S − self)/(C − 1)、自馬が null なら S/C。C−1=0(他馬非 null 0)→ NaN。
- **coverage の分子** は front_runner_rate(=closer_rate と同条件で非 null)で判定。coverage は leave-one-out しない(レース全体の脚質判明率、自馬含む診断値)。

## registry 登録

- 7 列を REGISTRY に追加: source=`derived`(他馬 as-of 由来)、timing=`PRE_ENTRY`(出馬表時点で読める展開)、missing_policy=`NULL`。
- FEATURE_GROUPS: 7 列すべてに group=`pace_scenario`。
- FEATURE_VERSION: `features-008` → `features-009`。
- STATIC_COLUMNS には **追加しない**(as-of/field 由来 ⇒ materialized_columns に自動収録)。
- ALL_COLUMNS は registry から自動導出(既存機構)。

## materialization（025 連携）

- `materialize.build_asof_features` に pace_scenario ブロックを追加(history/020/023 pace/026 pedigree/030 lowcost と同じ単一 as-of 源)。in-memory builder・serving fallback と同一関数。
- 新ソース列なし(running_style/corner_orders は 023 で既に loader がロード&fingerprint 包含済み)⇒ `source_fingerprint` の射影列は無改修。
- bit パリティ: materialize==in-memory の `build_feature_matrix` が `assert_frame_equal(check_exact=True, check_dtype=True)`。
- serving 未来レース(parquet 非カバー): 単一レース fallback が build_pace_scenario_features を当該レースだけ実行(生成と同一実装、コスト 1 レース分)。

## リーク属性（憲法 II 必須記載）

- **source**: 同レース他馬の過去(strictly-before)実績由来の二次集計(派生)。
- **利用可能タイミング**: PRE_ENTRY(出馬表確定時点。脚質は過去走由来、フィールドは entry_status 由来でいずれも予測時点既知)。
- **欠損処理**: NULL 伝播(0 埋め禁止)。coverage 列で Unknown 量を明示。
- **非特徴**: 今走 result/finish_order/corner_orders/running_style、オッズ/人気。
