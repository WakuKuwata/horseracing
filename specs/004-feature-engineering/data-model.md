# Data Model: 特徴量生成

新テーブルは MVP では作らない。既存データを読み、固定スキーマの FeatureMatrix を返す。本書は
FeatureRow の固定列と registry メタデータを正本化する。

## 入力 (読取、Feature 001/002 の既存テーブル)

| 用途 | 取得元 |
|---|---|
| レース・日付・条件 | `races` |
| 出走情報 (発走前 + 結果確定時) | `race_horses` |
| 結果・着順・上がり | `race_results` |
| 馬・血統名 | `horses` |
| 騎手・調教師 | `jockeys`, `trainers` |
| ラベル (採点側) | `labels.derive_labels` (本 feature は特徴量のみ。ラベルは学習側) |

## FeatureRow (固定スキーマ、1 行 = race-horse)

キー: `race_id`, `horse_id`(識別用、モデル入力ではない)。各特徴列は registry に metadata を持つ。
`availability_timing`: pre_entry/post_frame/post_weight/post_odds/pre_race/post_result。
MVP の特徴列は pre_entry/post_frame/post_weight のみを使う。`post_odds`/`pre_race`/`post_result` は
将来の特徴量タイミング用に予約 (enum には存在するが MVP では未使用。post_result はモデル入力から除外
する機構の対象)。
`missing_policy`: null=Unknown(0 と区別) / zero_ok(件数は 0 可)。

### 発走前静的 (US1)

| 列 | source | timing | missing |
|---|---|---|---|
| `venue_code` | races | pre_entry | null |
| `distance` | races | pre_entry | null |
| `track_type` | races | pre_entry | null |
| `going` | races | pre_entry | null |
| `weather` | races | pre_entry | null |
| `race_class` | races | pre_entry | null |
| `race_number` | races | pre_entry | null |
| `age` | race_horses | pre_entry | null |
| `sex` | race_horses | pre_entry | null |
| `frame` | race_horses | post_frame | null |
| `horse_number` | race_horses | post_frame | null |
| `jockey_id` | race_horses | pre_entry | null |
| `trainer_id` | race_horses | pre_entry | null |
| `weight` (馬体重) | race_horses | post_weight | null |
| `weight_diff` | race_horses | post_weight | null |

### 過去成績累積 (US1, as-of `race_date < R`、完走前提系は finished のみ)

| 列 | source | timing | missing |
|---|---|---|---|
| `career_starts` | race_horses/results 履歴 | pre_entry | zero_ok |
| `days_since_last` | 履歴 | pre_entry | null |
| `prev_finish` | results 履歴 (finished) | pre_entry | null |
| `prev_last3f` | results 履歴 (finished) | pre_entry | null |
| `avg_finish` | results 履歴 (finished) | pre_entry | null |
| `win_rate` | results 履歴 (finished) | pre_entry | null |

### 履歴件数 (US1, 非完走系・別系統・0 可)

| 列 | source | timing | missing |
|---|---|---|---|
| `cancel_count` / `exclude_count` / `stop_count` | 履歴 | pre_entry | zero_ok |
| `prev_was_cancel` / `prev_was_exclude` / `prev_was_stop` | 履歴 | pre_entry | zero_ok |

### フラグ (US1)

| 列 | 定義 | missing |
|---|---|---|
| `has_past_race` | career_starts > 0 | zero_ok |
| `is_debut` | career_starts == 0 | zero_ok |
| `past_race_count` | = career_starts | zero_ok |
| `is_low_history` | 1 <= career_starts <= 低履歴上限(既定 2) | zero_ok |

## 不変条件 (テストで検証)

- **INV-F1 (リーク)**: race R の過去成績/履歴件数特徴量は `race_date < R` の履歴のみを使う (同日・未来
  を使わない)。
- **INV-F2 (Unknown≠0)**: 出走歴ゼロの馬の過去成績系 (prev_*, avg_*, win_rate, days_since_last) は null
  で 0 でない。`is_debut=true`/`has_past_race=false`/`past_race_count=0`。
- **INV-F3 (完走前提)**: avg_finish/prev_last3f/win_rate は finished のみ。取消/除外 (DNS)・中止/失格
  (DNF) を含めない。career_starts は started を数える (DNF 含む、DNS 除く)。
- **INV-F4 (メタデータ強制)**: FeatureMatrix の全列が registry に登録され metadata を持つ。未登録列は
  fail-fast。結果確定 `odds`/`popularity` はモデル特徴量に含めない (混入は未登録列として検出)。
- **INV-F5 (結果後除外)**: `availability_timing='post_result'` の特徴量は `model_input_features()` から
  機械的に除外される。
- **INV-F6 (決定論)**: 同一入力・同一 as-of で FeatureMatrix が完全一致。

## FeatureRegistry (logical)

`name -> {source, availability_timing, missing_policy}` の宣言。`build_feature_matrix` が全特徴列を
検証する。`model_input_features()` は post_result を除外し、識別列 (race_id/horse_id) と非特徴列を除く。

## P2 (deferred)

- **target encoding map** (US3): train 境界以前で fit したカテゴリ→値。未知は既定値。
- **materialized feature store** (US4): date range / fold 単位のキャッシュ (非破壊)。
