---
description: "Task list — Materialized 特徴量の serving/training 結線 + 単一ロード化 (055)"
---
# Tasks: Materialized 特徴量の serving/training 結線 + 単一ロード化

## Phase 1: Foundational — fp-v2 + 読み込み経路(US2 の土台。US1 結線より先 = 結線後に read 経路を作り直さない)
- [X] T001 [US2] features `materialize.py`: `_hash_frame` 値正準化(数値列→float64・他→str)+ manifest に `fingerprint_algo: "fp-v2"` 追加。単体テスト: int64/float64 同値ハッシュ一致・値変更で不一致・「窓内 all-int/窓外 NaN」列の窓非依存(旧実装では落ちる)・旧 manifest(algo 欠落)は型付きエラー+再 materialize 案内 — features/src/horseracing_features/materialize.py, features/tests/
- [X] T002 [US2] features `loader.py`: `load_frames` に下限引数(start_after: date | None = None、(start_after, end_date] の delta ロード用)。既定 None は挙動バイト不変(既存テスト無改修緑) — features/src/horseracing_features/loader.py
- [X] T003 [US2] features `builder.py`+`materialize.py`: fp_frames フルロード撤去 → (i) end_date>=data_through は frames を _restrict 再利用 (ii) end_date<data_through は delta ロード+concat で正準ハッシュ検証。検証スキップ内部パラメータ(既定=検証あり)。テスト: restrict+delta fingerprint == フル fingerprint(合成 DB)・ソース行改変で fail-closed・bit パリティ(materialized==in-memory, check_exact/check_dtype)維持 — features/src/horseracing_features/builder.py, features/tests/

## Phase 2: US1 — serving/training/live 結線(opt-in・既定 OFF)
- [X] T004 [US1] serving `pipeline.py`: `run_serving(use_materialized=False, materialized_path=None)` 透過 + `run_serving_backfill` は run 開始時 1 回検証→日ループは検証スキップ(D3)。テスト: use_materialized=True の予測 p == False とバイト一致・検証ヘルパ呼び出し 1 回の wiring・フラグ未指定は従来経路(既存テスト無改修) — serving/src/horseracing_serving/pipeline.py, serving/tests/
- [X] T005 [US1] serving `cli.py`: `predict`/`predict-backfill` に `--use-materialized [--materialized-path]`(既定パス `../artifacts/features.parquet` = weights_uri と同じ cwd=serving 相対規約、028 ops subprocess 互換) — serving/src/horseracing_serving/cli.py
- [X] T006 [P] [US1] training `dataset.py`+`cli.py`: dataset 構築に use_materialized 透過、`train-evaluate`/`model-eval` にフラグ(既定 OFF・出力不変) — training/src/horseracing_training/dataset.py, training/src/horseracing_training/cli.py
- [X] T007 [P] [US1] live `orchestrate.py`+`cli.py`: `refresh --use-materialized` を予測段(serving)のみに伝播(推奨段は特徴ビルドなし=非伝播)。wiring テスト — live/src/horseracing_live/orchestrate.py, live/src/horseracing_live/cli.py, live/tests/

## Phase 3: 検証・計測
- [X] T008 実 DB E2E(quickstart): fp-v2 で `features materialize` 再生成 → パリティ(assert_frame_equal exact)→ lgbm-042 予測 p バイト一致 → `predict-backfill --use-materialized` 冪等通し(skip_exists)→ stale シナリオ(ソース 1 行変更→型付きエラー→再 materialize 復旧)。**速度/ピーク RSS 計測を記録**(SC-001: ベースライン 59.2s/3.40GB → 目標 ~13s/≤3.40GB) — 記録先: specs/055-materialized-serving/spec.md 末尾 or CLAUDE.md サマリ
- [X] T009 全スイート緑(features/serving/training/live)+ ruff クリーン。フラグ未指定経路の無改修緑を確認
- [X] T010 [P] CLAUDE.md 055 サマリ更新(計測値・fp-v2 移行注意=要 1 回再 materialize を含む)

## Dependencies
- T001 → T003(fp-v2 が delta 検証の前提)/ T002 → T003(delta ロード)/ T003 → T004(read 経路確定後に結線)
- T004 → T005(pipeline API 確定後に CLI)/ T006・T007 は T003 後に並列可 [P]
- T008 → T009 → T010

## MVP scope
T001–T005(US2 土台 + serving 結線)= 予測/backfill が高速化する最小増分。T006/T007 は独立追加。
