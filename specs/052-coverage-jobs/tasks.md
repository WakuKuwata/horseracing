---
description: "Task list — データ被覆率 + ジョブ履歴 (052)"
---
# Tasks: データ被覆率 + ジョブ履歴

## Phase 1: API
- [X] T001 [US1] `api/queries.py` `coverage_by_date(session, date_from, date_to)` — 日別グループ化集計(races/odds/results/active予測/推奨)+ active モデル解決
- [X] T002 [US1] `api/schemas.py` + `api/routers/coverage.py`: `GET /coverage`(範囲ガード 422・active_model_version 付き)
- [X] T003 [US2] `api/queries.py` `list_jobs(...)` + `api/routers/jobs.py`: `GET /jobs`(フィルタ・limit cap・created_at DESC)
- [X] T004 [P] api tests: 被覆集計の正しさ・active 不在=0・範囲ガード 422・jobs フィルタ/順序/cap・全 path GET 維持
- [X] T005 OpenAPI 再生成(front 期待リスト更新 + front/admin snapshot 同期 + 型再生成)→ 両 drift-check 緑

## Phase 2: admin SPA
- [X] T006 [US1] CoveragePage(直近 30 日既定・日別テーブル・予測<レース数の穴ハイライト)+ ナビ追加
- [X] T007 [US2] JobsPage(status/job_type フィルタ・エラー行表示)
- [X] T008 [P] admin tests(被覆描画・穴ハイライト・範囲 422 表示・jobs フィルタ・null 安全)

## Phase 3: 検証
- [X] T009 実 DB E2E: 2025-01 被覆ほぼ全埋まり・未 backfill 期間は予測 0・jobs 実データ返却
- [X] T010 [P] CLAUDE.md 052 サマリ(マージ時)
