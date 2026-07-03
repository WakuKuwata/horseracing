---
description: "Task list — アクション起動 (053)"
---
# Tasks: アクション起動

## Phase 1: ops ジョブ
- [X] T001 [US1] ops `__init__.py` JOB_TYPE_REFRESH_RANGE + `enqueue.py` `enqueue_refresh_range(session, date_from, date_to)`(ACTIVE dedup・advisory lock)
- [X] T002 [US1] `runner.py` `run_refresh_range`(subprocess `uv run --project live … refresh`・timeout 3600・exit→status マップ)+ worker dispatch/claimable
- [X] T003 [US1] `routers/refresh_range.py` `POST /refresh-range`(from≤to・≤35 日ガード 422)+ app 結線
- [X] T004 [P] ops tests: 契約(202/422/dedup)・runner terminal マップ(monkeypatch subprocess)・境界(live 非 import)
- [X] T005 ops-openapi 再生成 → front/admin snapshot+型再生成・drift 緑

## Phase 2: admin
- [X] T006 [US2] admin vite /ops proxy + opsClient + ops-schema 型
- [X] T007 [US2] CoveragePage: 日行「この日を更新」+ 範囲「この範囲を更新」(確認必須・pending disabled・job_id 表示+ジョブ履歴誘導)
- [X] T008 [P] admin tests(確認→POST body・pending disabled・202 表示・エラー表示)

## Phase 3: 検証
- [X] T009 実 DB E2E: enqueue→worker 実行→SUCCEEDED(既 backfill 日=skip 系)・dedup reused
- [X] T010 [P] CLAUDE.md 053 サマリ(マージ時)
