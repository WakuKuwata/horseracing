---
description: "Task list — admin 土台 + モデルレジストリ (051)"
---
# Tasks: admin 土台 + モデルレジストリ

## Phase 1: API (US1)
- [X] T001 [US1] `api/schemas.py`: `ModelVersionRow`/`ModelListResponse`(全指標 nullable)
- [X] T002 [US1] `api/queries.py` `list_model_versions(session)` — active 優先 → created_at DESC → model_version、metrics_summary 抽出(欠落=null)
- [X] T003 [US1] `api/routers/models.py`: `GET /api/v1/models`(app 結線)
- [X] T004 [P] [US1] api tests: 一覧順序・metrics 欠落 null・全 path GET 維持・空 DB で 200-empty
- [X] T005 OpenAPI 再生成(front/openapi.json 純追加 + endpoint 期待リスト更新 + schema.d.ts)→ front drift-check 緑

## Phase 2: admin SPA (US2)
- [X] T006 [US2] `admin/` パッケージ新設(Vite+React+TS+Vitest+MSW、front 同型 tooling・localhost proxy)
- [X] T007 [US2] admin openapi snapshot + 型生成 + drift-check(015 同型)
- [X] T008 [US2] レジストリ一覧ページ(active バッジ・LogLoss/AUC/ECE・feature_version・train_through・作成日時)
- [X] T009 [US2] モデル詳細ページ(calibration bins・importance・adoption 理由、未収録=typed 表示)
- [X] T010 [P] [US2] admin tests(一覧描画・null 安全・active バッジ・404 表示・drift-check)

## Phase 3: 検証
- [X] T011 実 DB E2E: `GET /api/v1/models` が lgbm-042 active 先頭で返る・admin 画面で一覧/詳細確認
- [X] T012 [P] CLAUDE.md 051 サマリ(マージ時)
