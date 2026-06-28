# Contract: 特徴量 materialization 基盤 (025)

スキーマ変更なし。契約は (a) parquet/manifest 形式、(b) 生成 CLI、(c) builder read API、(d) 不変条件。

## 1. parquet スキーマ契約（`artifacts/features.parquet`）
- 列 = `race_id`(str, non-null), `horse_id`(str, non-null) + as-of 列（data-model §1、registry から機械導出）。
- 列順固定（識別子→registry 順）。明示 dtype（float64 維持、Int64 nullable は null 保持、0 で埋めない）。
- 行は `(race_id, horse_id)` で決定論ソート。
- **static/current-race 列は含めない**（builder が計算）。

## 2. manifest 契約（`artifacts/features.manifest.json`）
`data_from`, `data_through`, `n_rows`, `feature_version`, `content_hash`, `generated_at`, **`source_fingerprint`**, `materialized_columns`。

## 3. 生成 CLI（features）
- `features materialize [--from --to] [--out artifacts/features.parquet]`: 全プールの as-of 特徴を計算（既存ブロック関数）→ parquet + manifest を出力。決定論（2 回実行で content_hash 一致）。DB read-only、書き込みなし。

## 4. builder read API（features）
- `assemble_feature_matrix(..., materialized: Path|None=None, use_materialized: bool=False)`:
  - `use_materialized` かつ parquet 有効（coverage+fingerprint+version 合格）→ as-of 列を parquet から merge、static は計算。
  - 未カバーの**未来レース**のみ block 関数で fallback 計算（audit warning）。
  - parquet 無効/古い（fingerprint 不一致）/履歴未カバー → **fail-closed**（明示エラー、黙って古い値を使わない）。
  - `use_materialized=False`（既定）→ 現行 in-memory 経路（パリティ基準）。
- 出力は両経路で **bit 一致**（ALL_COLUMNS）。

## 5. 不変条件（テストで保証）
- **パリティ**: materialize 経路 == in-memory 経路の build_feature_matrix（check_exact, check_dtype, 列順）。予測（win/top2/top3）一致。
- **決定論**: 生成 2 回で parquet/manifest content_hash 一致。
- **staleness**: source fingerprint 不一致（範囲内 backfill 含む）→ fail-closed（黙って古い値 0 件）。
- **単一実装**: 同一 target race で generator 出力 == fallback 出力。materialize 列に static/current-race 0 件。
- **leak**: materialize 後に target/同日/未来の結果を変更しても当該特徴不変。
- **no-schema**: db migration head 不変、新 ORM テーブル 0、FEATURE_VERSION 不変。
