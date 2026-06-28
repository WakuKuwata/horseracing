# Data Model: 特徴量 materialization 基盤 (025)

**スキーマ変更なし**（DB テーブル/カラム追加なし）。materialize 先は artifacts 配下のファイル。

## 1. materialized as-of 特徴 parquet（`artifacts/features.parquet`, 非コミット）
- **粒度**: per-(race_id, horse_id)。
- **列**: 識別子 `race_id`, `horse_id` + **as-of/過去由来特徴のみ**（registry から機械導出）:
  - history: career_starts/days_since_last/prev_finish/prev_last3f/avg_finish/win_rate/cancel_count/exclude_count/stop_count/prev_was_cancel/prev_was_exclude/prev_was_stop/has_past_race/is_debut/past_race_count/is_low_history
  - 020: avg_last3_finish/recent_win_rate（recent_form）, dist_band_win_rate/dist_band_avg_finish/surface_win_rate（aptitude）, class_transition
  - 020 human_form: jockey_win_rate/trainer_win_rate
  - 023: rel_last3f_avg/rel_last3f_best/rel_time_avg/finish_diff_avg/finish_diff_best（pace_time）, rel_corner_pos_avg/front_runner_rate/closer_rate（position_style）
- **非収録（builder が現行計算）**: static/current-race（venue_code/distance/track_type/going/weather/race_class/race_number/age/sex/frame/horse_number/jockey_id/trainer_id/weight/weight_diff/field_size）。
- **スキーマ契約**: 列順固定・明示 dtype（float64 保持・nullable は null・ID non-null）・`(race_id, horse_id)` 決定論ソート。
- **不変条件**: 各行 = 現行 in-memory のブロック関数出力と bit 一致。pool-end 非依存（strict-before）。

## 2. manifest（`artifacts/features.manifest.json`, 非コミット）
- `data_from` / `data_through`（race_date 範囲）
- `n_rows`
- `feature_version`（= registry.FEATURE_VERSION、据え置き）
- `content_hash`（parquet 値の決定論ハッシュ）
- `generated_at`
- **`source_fingerprint`**: 特徴計算入力（races/race_horses/race_results の射影カラム）の決定論ハッシュ。staleness/backfill 検知の要。
- `materialized_columns`: 収録 as-of 列名（機械導出の記録）。

## 3. coverage / staleness 判定（read 時）
- 入力: 要求 (race_id,horse_id) 集合、現行 DB の source_fingerprint。
- 合格条件: parquet が全要求キーを含む ∧ manifest.source_fingerprint == 現行 fingerprint ∧ manifest.feature_version == registry.FEATURE_VERSION。
- 不合格: **fail-closed**（明示エラー）。例外: parquet カバー外の**未来レース**のみ fallback 計算（block 関数）＋ audit warning。

## 4. 計算源（単一実装）
- as-of 値の唯一の生成元 = 既存 `build_history_features` / `build_extra_features` / `build_human_form_features` / `build_pace_features`（`Frames` から計算）。
- 生成フェーズ・serving fallback・パリティ比較はすべてこれを呼ぶ（二重実装禁止）。

## エンティティ関係
- 1 race → N 出走馬。各馬の as-of 特徴が parquet（履歴）または fallback（新規レース）から供給。
- builder: parquet（as-of, opt-in 時）+ static（常時計算）→ ALL_COLUMNS 固定スキーマ。
- すべてキャッシュ/計算。DB 書き込みなし、スキーマ不変（head 不変）。
