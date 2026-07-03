---
description: "Task list — win 的中/回収バックテスト表示 (049)"
---
# Tasks: win 的中/回収バックテスト表示

## Phase 1: API 純ロジック + read 経路 (US1)
- [X] T001 [US1] `api/src/horseracing_api/backtest.py`(新・betting 非 import): `WinRealized` dataclass + `win_realized(selection, market_odds_used, *, finish_map, n_winners)` — settled/hit/dead_heat/realized_return/realized_roi の純計算(void=hit None・DNF=不的中・同着=dead_heat)
- [X] T002 [P] [US1] `api/tests/`: win_realized の 的中/不的中/void/同着/DNF/非 win null(pure unit、DB 不要)
- [X] T003 [US1] `api/queries.py` `race_finish_map(session, race_id)` — RaceResult 1 回ロード→(dict[horse_id,(finish_order,result_status)], n_winners)
- [X] T004 [US1] `api/schemas.py` `RecommendationRow` に settled/hit/dead_heat/realized_return/realized_roi(全 nullable・既定 null)
- [X] T005 [US1] `api/routers/recommendations.py`: finish_map をレース毎 1 回取得、win 行に win_realized 適用(生 selection dict の horse_id 使用)
- [X] T006 [P] [US1] `api/tests/` integration: settled レースで realized 返却・未 settled で null・全 path GET 維持・api が betting を import しない境界テスト

## Phase 2: OpenAPI 同期
- [X] T007 OpenAPI 再生成(front/openapi.json key-sort + schema.d.ts)→ drift-check・read-only test 緑

## Phase 3: front 表示 (US1 + US2)
- [X] T008 [US1] `front/src/components/RecommendationPanel.tsx`: win settled 行に「結果」列グループ(的中/不的中/void + 実現回収 ×odds + realized_roi)を pseudo 列と分離、同着注記、realized は `<ResultBadge>`(real・非 pseudo)
- [X] T009 [P] [US1] `front/` types 反映(types.ts 再エクスポート)+ RecommendationPanel.test.tsx: 結果列描画・pseudo-label 不変(realized real 値は data-pseudo 無し)
- [X] T010 [US2] RecommendationPanel に過去実績サマリ(n_settled/n_hit/hit_rate/mean realized_roi/recovery_rate)を front 集計・**retrospective ラベル必須・損益色なし・ソートなし**(021 規律)+ test

## Phase 4: 検証
- [X] T011 実 DB E2E(2025-01-05 の settled レース): win 推奨に的中/回収表示・同着例確認。api/front スイート緑・drift-check 緑・migration head 不変
- [X] T012 [P] CLAUDE.md 049 サマリ(マージ時)
