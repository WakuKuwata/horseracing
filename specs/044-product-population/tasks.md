---
description: "Task list — 製品をデータで満たす・予測backfill (044)"
---

# Tasks: 製品をデータで満たす — 予測 backfill と一括 populate

**Input**: [plan.md](plan.md) / [spec.md](spec.md)（codex CLI 利用不可のため single-opinion）

## Phase 1: US1 - 予測 backfill (P1)
- [X] T001 [US1] `serving/src/horseracing_serving/pipeline.py`: run_serving の per-race ループ本体を `_predict_persist(session, model, rid, feature_rows, logic_version)` に抽出(run_serving は挙動不変=既存テスト緑)
- [X] T002 [US1] `pipeline.py`: `run_serving_backfill(session, *, date_from, date_to, model_version=None, force=False)` = モデル1回ロード・日ごとに build_feature_matrix(end_date=D)・active(=model.model_version)run が無いレースのみ _predict_persist(force で無視)・BackfillCounts(generated/skip_exists/skip_no_started/error)・per-day try/except 隔離
- [X] T003 [US1] `serving/src/horseracing_serving/cli.py`: `predict-backfill --from --to [--model-version] [--force] [--database-url]` サブコマンド → run_serving_backfill → reconciliation print
- [X] T004 [P] [US1] `serving/tests/`: p-parity(backfill の win_prob == 同一レースの run_serving(date=D))・冪等(2回目は skip_exists で新 run 無し)・force で再生成・reconciliation 件数一致・per-day 例外隔離
- [X] T005 [P] [US1] `serving/tests/`: run_serving 既存挙動不変(リファクタ回帰)+ リーク境界(結果変更で予測不変=既存 leak テスト透過)

## Phase 2: US2 - 実データ populate & 検証 (P1)
- [X] T006 [US2] 実 DB: 最近範囲を predict-backfill → recommend-backfill(043)で populate、件数確認
- [X] T007 [US2] 実 DB/ブラウザ: RaceDetailPage が予測・スコア寄与・買い目を実データ表示(手動1レース以上)、RaceListPage に予測ありレース

## Phase 3: Polish
- [X] T008 [P] serving/betting/api lint+test 緑・read-only 014 不変
- [X] T009 [P] `CLAUDE.md` 044 サマリ(main マージ時に追記=分岐衝突回避)

## Dependencies
- T001(抽出)→ T002(backfill)→ T003(CLI)→ T004/T005。US2 は US1 後。
- 憲法: II(リーク不変)/III(p-parity・冪等テスト)/V(append-only)/VI(migration なし・read-only)。
