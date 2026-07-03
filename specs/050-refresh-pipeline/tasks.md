---
description: "Task list — 一括更新コマンド + 学習ウィンドウ記録 (050)"
---
# Tasks: 一括更新コマンド + 学習ウィンドウ記録

## Phase 1: betting コア抽出 (US1 前提)
- [X] T001 [US1] `betting/cli.py`: `_cmd_recommend_backfill` のループ本体を `recommend_backfill(session, *, date_from, date_to) -> dict`(counts 返却・per-race 例外隔離・日単位 pcal フィット維持)に抽出、CLI は wrap(出力同等)
- [X] T002 [P] [US1] betting スイート緑(既存 recommend-backfill テストが無改修で通る=挙動同等の証明)

## Phase 2: live refresh (US1)
- [X] T003 [US1] `live/orchestrate.py`: `refresh_range(session, *, date_from, date_to, force=False) -> RefreshReport`(serving run_serving_backfill → betting recommend_backfill の順・段間例外隔離)
- [X] T004 [US1] `live/cli.py`: `refresh --from --to [--force]` サブコマンド(両段サマリ表示)
- [X] T005 [P] [US1] `live/tests/`: wiring テスト(monkeypatch で呼出順序・引数・force 伝播・予測段失敗でも推奨段実行)

## Phase 3: 学習ウィンドウ記録 (US2)
- [X] T006 [US2] `training/artifacts.py`: `summary["training"]` に train_through / n_model_rows / n_calib_rows を追加(metadata.json と同値)
- [X] T007 [P] [US2] training テスト: 保存後の metrics_summary に 3 キーが入る assert(既存 artifacts テスト拡張)

## Phase 4: 検証
- [X] T008 実 DB E2E: 既 backfill 済み日で `live refresh` → 両段 skip 系で完走(冪等通し)。live/betting/training スイート緑
- [X] T009 [P] CLAUDE.md 050 サマリ(マージ時)
