# Quickstart: 特徴量 materialization 基盤 (025)

実データ（horseracing DB, [[local-db-setup]], 2007–2024）+ 合成データで各 US の受入を確認する検証ガイド。実装詳細は tasks.md。

## 前提
- `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`
- db head 不変（スキーマ変更なし）。materialize 先は `artifacts/`（.gitignore 済み）。

## US1: 生成フェーズ
1. `features materialize --out artifacts/features.parquet` → parquet + `artifacts/features.manifest.json` 出力。
2. manifest に data_from/through・n_rows・feature_version・content_hash・**source_fingerprint**・materialized_columns が記録される。
3. 2 回実行で content_hash 一致（決定論）。
4. 期待: SC-003。

## US2: パリティ + read 経路
1. パリティテスト（合成/実データ）: `assemble_feature_matrix(use_materialized=True)` と `=False` の出力を `assert_frame_equal(check_exact=True, check_dtype=True)` で**全列一致**。
2. materialize 経由と in-memory 経由で学習した予測（win/top2/top3）が一致。
3. **staleness**: parquet 生成後に範囲内の race_horses/race_results を 1 行変更 → fingerprint 不一致 → `use_materialized=True` の build が **fail-closed**（黙って古い値を出さない）。
4. parquet 削除/未カバー → fail-closed。
5. 期待: SC-001/002/004/008。

## US3: serving fallback（未来レース）
1. parquet に無い新規（未確定）レースを `use_materialized=True` で build → 当該レースのみ block 関数で fallback 計算され、生成フェーズと同値。
2. 「同一合成 target race で generator 出力 == fallback 出力」契約テスト。
3. 期待: SC-005/SC-009。

## 横断ゲート
- **leak**: materialize 後に target/同日/未来レースの結果を変更 → 当該 target の as-of 特徴が不変（SC-008, 憲法 II）。
- **単一実装**: materialize 列に static/current-race 列が 0 件（registry 機械導出, SC-009）。
- **no-schema**: db head 不変・`__tablename__` 追加なし・FEATURE_VERSION 不変（SC-006）。
- **性能**: 生成 1 回の所要時間/メモリを実測し予算内（SC-007）。eval 反復が parquet read で済むことを確認。
- lint/test: `uv run ruff check` + `uv run pytest`（features）緑。training/eval/serving は透過（既存テスト緑のまま）。
