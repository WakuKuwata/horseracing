# Data Model: relative_ability feature group (059)

**No DB schema change** — features only. No migration. `race_predictions` / `recommendations` /
OpenAPI / ORM すべて不変。以下は feature registry と materialize 契約の記述。

## Feature group: `relative_ability` (FEATURE_VERSION features-014)

全列: `source="relative"`, `timing=PRE_ENTRY`, `missing_policy=NULL`, dtype **float64**,
`materialized=True`(STATIC_COLUMNS に含めない → `materialized_columns()` が自動導出)。

### Deviation columns (leave-one-out, 11)

各列 = 自分の as-of 値 − 自分を除いた started フィールドの平均。入力列は features-013 で既存。

| feature column | input as-of column | 由来ブロック |
|---|---|---|
| `win_rate_vs_field` | win_rate | history |
| `recent_win_rate_vs_field` | recent_win_rate | history(020) |
| `place_rate_vs_field` | place_rate | history(030) |
| `show_rate_vs_field` | show_rate | history(030) |
| `dist_band_win_rate_vs_field` | dist_band_win_rate | history(020) |
| `surface_win_rate_vs_field` | surface_win_rate | history(020) |
| `rel_time_avg_vs_field` | rel_time_avg | pace(023) |
| `rel_last3f_avg_vs_field` | rel_last3f_avg | pace(023) |
| `finish_diff_best_vs_field` | finish_diff_best | pace(023) |
| `jockey_win_rate_vs_field` | jockey_win_rate | human_form(020) |
| `trainer_win_rate_vs_field` | trainer_win_rate | human_form(020) |

### Field percentile rank columns (2)

| feature column | input | 意味 |
|---|---|---|
| `win_rate_field_rank` | win_rate | started 母集団内の総合能力パーセンタイル |
| `rel_time_avg_field_rank` | rel_time_avg | started 母集団内のスピードパーセンタイル |

**Excluded(事前確定)**: `venue_win_rate` 系(coverage 11% → inert・ノイズ源、research D1)。

## Registry changes (`features/registry.py`)

1. `REGISTRY` に上記 13 列を追加(各 `FeatureMeta("relative", _T.PRE_ENTRY, _M.NULL)`)。
2. `FEATURE_GROUPS` に 13 列 → `"relative_ability"`。
3. `FEATURE_VERSION = "features-014"`。
4. `STATIC_COLUMNS` は**変更しない**(新群は as-of 派生 → materialized 対象)。
5. `materialized_columns()` は registry から機械導出 → 新群を自動包含(手当て不要)。

## Materialize contract (`features/materialize.py`)

- `build_asof_features` の merge 済み `out` を入力に `build_relative_ability_features` を呼び、
  結果を `out` に merge → 既存の `cols = [*_KEYS, *materialized_columns()]` 選択が新群を拾う。
- `source_fingerprint`: **拡張不要**(新ソース生列を読まない)。`_HORSE_FP_COLS` も不変。
- `MANIFEST_VERSION` 不変・`feature_version` は features-014 に更新(旧 parquet は要 1 回再生成
  =fail-closed で気づける、055 と同型)。

## Invariants

- **bit-parity**: materialized 経路 == in-memory `build_feature_matrix` が新群込みで
  `assert_frame_equal(check_exact=True, check_dtype=True)`。
- **leak boundary**: 対象レースの結果/オッズ/同日他馬値を改変 → 13 列不変(leak-guard test)。
- **probability**: 009 win→joint・Σ 整合・Unknown/取消除外 は不変(特徴追加のみ)。
- **missing**: 全列 NaN 許容(0 埋め禁止)。フィールド他馬非NaN 数 0 → NaN。
