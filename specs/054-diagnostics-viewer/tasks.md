---
description: "Task list — 診断永続化 + ビューア (054)"
---
# Tasks: 診断永続化 + ビューア

## Phase 1: 永続化 (US1)
- [X] T001 [US1] db: `DiagnosticRun` ORM + migration 0009(diagnostic_runs、index (kind, computed_at DESC))+ db テスト
- [X] T002 [US1] features/live の migration head assert を 0009 に更新(040 前例)
- [X] T003 [US1] eval `diagnostics_store.py`: `save_diagnostic_run(session, kind, report→payload, ...)` + テスト
- [X] T004 [US1] training CLI `segment-diagnostic --persist`(表示不変・logic_version 記録)+ テスト

## Phase 2: 読み出し + ビューア (US2)
- [X] T005 [US2] api `GET /diagnostics/segment-edge`(最新 run 転記・404 diagnostic_unavailable)+ テスト
- [X] T006 OpenAPI 同期(front 期待リスト+front/admin snapshot+型)→ drift 緑
- [X] T007 [US2] admin DiagnosticsPage(軸別テーブル・鮮度表示・SECONDARY 但し書き常時・ソート/損益色なし・未永続化案内)+ ナビ + テスト

## Phase 3: 検証
- [X] T008 実 DB E2E: 短窓 --persist → API → 表示値一致。全スイート緑・head=0009
- [X] T009 [P] CLAUDE.md 054 サマリ(マージ時)
