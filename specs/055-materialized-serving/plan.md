# Implementation Plan: Materialized 特徴量の serving/training 結線 + 単一ロード化

**Branch**: `055-materialized-serving` | **Date**: 2026-07-05 | **Spec**: [spec.md](spec.md)

## Summary

025 の parquet materialization(bit-parity・fail-closed)を serving/training に opt-in 結線し(US1)、読み込み経路の DB 二重ロードを「fingerprint の値正準化ハッシュ + delta 検証」で削減する(US2)。実測ベース: 現行 59.2s/3.40GB → materialized(二重ロード) 22.3s/3.13GB → 目標 ~13s/~2.5GB。新特徴・新ロジック・スキーマ変更なし、FEATURE_VERSION 不変(features-012)。

## Technical Context

**Language/Version**: Python 3.12 / **Primary Dependencies**: pandas, pyarrow(parquet), SQLAlchemy 2.0(既存のみ・新依存なし) / **Storage**: PostgreSQL 16(read)+ `artifacts/features.parquet`(生成物・非コミット) / **Testing**: pytest + testcontainers(features/serving/training/live 既存スイート) / **Project Type**: 既存モノレポの features/serving/training/live 4 パッケージ改修 / **Performance Goals**: 特徴量ビルド 59.2s→~13s・ピーク RSS ≤3.40GB(目標 ~2.5GB)/ **Constraints**: bit パリティ非交渉(`assert_frame_equal(check_exact=True, check_dtype=True)`)・fail-closed staleness 維持・既定 OFF 後方互換 / **Scale/Scope**: 956,409 行×74 as-of 列 parquet(133MB)、24GB RAM マシン

## Constitution Check

- [x] **I. データ契約**: PASS — race_id/スキーマ/ラベル一切不変。読み込み経路の最適化のみ。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — 025 の as-of は per-row strictly-before でプール末尾非依存(実証済み)。本 feature は特徴値を一切変えない(bit パリティで機械保証)。fingerprint 正準化はステールネス検知の話でありデータフローに非介入。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — モデル/特徴変更なし。採否ゲート不要(インフラ)。パリティ+予測 p バイト一致が受入基準。
- [x] **IV. 確率整合性**: N/A — 確率導出に触れない。
- [x] **V. 再現性・監査**: PASS — parquet は DB から決定論再生成・fail-closed 維持・自動フォールバック禁止(FR-002)。manifest に fingerprint_algo を追記し旧 manifest を明示的に無効化。
- [x] **VI. feature 分割規律**: PASS — スキーマ/API/openapi 不変・UI なし・migration なし。
- [x] **品質ゲート**: codex 直近 4 feature 3 回連続起動失敗 → 見送り宣言・single-opinion(spec 記載)。

## Phase 0: Research(実測・実装調査済み — 本 plan 内に集約、research.md は省略)

**R1. 二重ロードの根本原因**: `_hash_frame` は `pd.util.hash_pandas_object` を非正準化で使用 → **dtype 感受性あり**(int64 と float64 の同値が別ハッシュ)。materialize 時はフルプール `load_frames(None)` で計算するため、検証時も同一条件のフルプールロードが必要だった(builder.py の fp_frames)。窓ロードの dtype はプール内容依存(025/026 の static int→float ドリフト前例)。

**決定 D1 — fingerprint 正準化(fp-v2)**: `_hash_frame` をハッシュ前に列正準化する: 数値列→`astype("float64")`、それ以外(object/日付/bool)→str 化。「同じ値集合なら同じハッシュ」がロード窓に依存せず成立。manifest に `fingerprint_algo: "fp-v2"` を追加し、フィールド欠落(旧 manifest)は「再 materialize が必要」の型付きエラー(黙って通らない)。
*代替案*: (a) 現状維持で二重ロード継続 — US2 不成立。(b) loader の dtype 明示固定(astype スキーマ) — 特徴値の dtype に触れるためパリティ破壊リスクが大きく、fingerprint だけ正準化する方が影響半径が小さい。

**決定 D2 — delta 検証(特徴 frames は窓ロードのまま)**: 特徴用 frames は従来どおり `load_frames(end_date=)`(窓ロード)を維持 — static ブロックの dtype パリティを構成的に保証(026 の教訓、restrict-from-superset はしない)。fingerprint 検証は (i) `end_date >= data_through` → frames を `_restrict(data_through)` して再利用(追加ロードゼロ)、(ii) `end_date < data_through` → **(end_date, data_through] の delta のみ追加ロード**(loader に下限引数を追加)し、`concat(restrict(frames), delta)` を正準ハッシュ。フルプール再ロード(~10s)が消え、delta は通常数日分(ミリ秒〜秒)。
*代替案*: `max(end_date, data_through)` で 1 回ロードして特徴側を in-memory restrict — static dtype ドリフトの再導入リスク(026 前例)で却下。

**決定 D3 — backfill の検証 1 回化**: `run_serving_backfill(use_materialized=True)` は run 開始時に 1 回だけ fingerprint 検証(検証専用ヘルパ)し、日ループの build には検証スキップ(内部パラメータ、既定は検証あり)を渡す。単一プロセス内でソース不変の前提(spec Assumptions)。日ごとのコスト: 窓ロード ~10s + parquet 読込/merge ~2s。

**R2. 結線ポイント(実装調査)**: serving [pipeline.py:62](../../serving/src/horseracing_serving/pipeline.py)(run_serving)/ 同 :140(run_serving_backfill 日ループ)、training [dataset.py:94](../../training/src/horseracing_training/dataset.py)、live orchestrate(refresh の予測段)。すべて `build_feature_matrix` 呼び出しに `use_materialized`/`materialized_path` を透過するだけ。CLI: serving `predict`/`predict-backfill`、training `train-evaluate`/`model-eval`、live `refresh` に `--use-materialized [--materialized-path]` を追加(既定 OFF)。

**R3. パス解決**: ops は serving を `cwd=serving/` の subprocess で呼ぶ(028)ため、serving CLI の既定 parquet パスは `../artifacts/features.parquet`(weights_uri と同じ相対規約)。features CLI の既定は `artifacts/features.parquet`(repo root 起点)のまま。

## Phase 1: Design

**data-model.md / contracts/**: N/A — 新エンティティ・スキーマ変更・API/openapi 変更なし(manifest への `fingerprint_algo` 1 フィールド追記のみ、生成物であり契約外)。

### Source Code(変更閉包)

```text
features/src/horseracing_features/materialize.py   # D1: _hash_frame 正準化 + fingerprint_algo、D2: delta 検証ヘルパ、検証スキップ内部パラメータ
features/src/horseracing_features/loader.py        # D2: load_frames に下限(start_after)引数を追加(既定 None=挙動不変)
features/src/horseracing_features/builder.py       # D2/D3: build_feature_matrix の fp_frames フルロード撤去→restrict+delta、skip_verify 内部パラメータ
features/src/horseracing_features/cli.py           # materialize が fp-v2 manifest を書く(read 側の互換エラー文言)
serving/src/horseracing_serving/pipeline.py        # US1: run_serving / run_serving_backfill(検証1回化)に opt-in 透過
serving/src/horseracing_serving/cli.py             # --use-materialized / --materialized-path
training/src/horseracing_training/dataset.py       # US1: use_materialized 透過
training/src/horseracing_training/cli.py           # train-evaluate / model-eval にフラグ
live/src/horseracing_live/orchestrate.py + cli.py  # refresh の予測段へフラグ伝播
features/tests/ serving/tests/ live/tests/         # 下記テスト
```

**Structure Decision**: 既存モノレポ構造のまま(新パッケージ・新ディレクトリなし)。変更は features(読み込み経路)+ 3 呼び出し元(serving/training/live)の透過パラメータ追加に閉じる。

### テスト設計(パリティ非交渉の機械固定)

1. fp-v2 単体: 同値 int64/float64 フレームのハッシュ一致・値変更で不一致・**窓非依存**(合成プールで「窓内 all-int / 窓外 NaN」の列を作り、窓ロード相当とフル相当のハッシュ一致を assert = 旧実装では落ちるテスト)。
2. delta 検証: `restrict(frames)+delta` の fingerprint == フルロード fingerprint(合成 DB)。ソース行改変で fail-closed 例外。
3. 旧 manifest(fingerprint_algo 欠落)→ 型付きエラーで「features materialize を再実行」を案内。
4. bit パリティ(既存拡張): materialized 経路(D2 統合後) == in-memory 経路、`check_exact=True, check_dtype=True`(合成 + 実 DB E2E)。
5. serving: run_serving(use_materialized=True) の予測 p == False の p(バイト一致)。backfill 検証 1 回化の wiring テスト(検証ヘルパ呼び出し回数=1)。
6. 後方互換: フラグ未指定で従来経路(既存テスト無改修緑)。

### quickstart(実 DB 検証手順)

```bash
# 1) fp-v2 で parquet 再生成(旧 manifest は無効化される)
DATABASE_URL=... uv run --project features python -m horseracing_features materialize
# 2) パリティ + 速度/メモリ計測(SC-001/002):
#    build_feature_matrix(use_materialized=True) == in-memory を assert_frame_equal、time/ru_maxrss 記録
# 3) serving 単発: 予測 p バイト一致(lgbm-042、任意の既存レース)
uv run --project serving python -m horseracing_serving predict --race-id <rid> --use-materialized --database-url ...
# 4) backfill 冪等通し(SC-004): 既 backfill 済み範囲で skip_exists 完走
uv run --project serving python -m horseracing_serving predict-backfill --from 2024-12-28 --to 2024-12-28 --use-materialized --database-url ...
# 5) stale シナリオ: ソース 1 行 UPDATE → build が型付きエラー → 再 materialize で復旧(SC-003)
```

## Complexity Tracking

違反なし — 表不要(スキーマ/API/確率/特徴値すべて不変のインフラ結線)。

## Progress Tracking

- [x] Phase 0: Research(実測 + 実装調査、本 plan に集約)
- [x] Phase 1: Design(変更閉包・テスト設計・quickstart)
- [x] Phase 2: tasks.md(/speckit-tasks)
- [x] 実装 + 実 DB E2E + 計測記録(結果: 59.2s→17.0s(3.5x)・RSS 3.40→3.10GB・パリティ bit 一致・lgbm-042 p バイト一致・stale fail-closed・詳細は CLAUDE.md 055 サマリ)

**実装ノート(D1 改訂)**: fp-v2 は当初の「数値→float64 のみ正準化」では不十分だった — 空 delta(未開催日の race_results 0 行)の read_sql は全列 object になり、concat で float64 列が object 劣化して正準化経路が分岐しハッシュ不一致(実 DB で発覚・合成テストは通過)。最終形は**全列を文字列の値正準形に統一**(missing→""・数値は repr(float)・list は str)+ 空 delta の concat スキップ。回帰テスト(object-Decimal 同値・空 concat 劣化)で機械固定。
