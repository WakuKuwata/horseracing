# Specification Quality Checklist: read-only 予測配信 API

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-06-25
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- 憲法 VI（契約先行）: 読み取り専用 API を 014 で確定 → front(React/Vite) は 015。OpenAPI が契約。
- codex(codex-rescue) second opinion の CRITICAL/HIGH を反映:
  1. **書込禁止（CRITICAL）** → `/recommendations` は永続行 SELECT のみ。`generate_exotic_recommendations`（書込）を呼ばない
     （FR-002/FR-006/SC-005）
  2. **パッケージ結合（CRITICAL）** → `api/` は ORM + 純粋確率ヘルパのみ依存、betting 書込に非依存（FR-002）
  3. **prediction_run 選択の決定論（CRITICAL）** → 採用優先 → computed_at DESC → run_id タイブレーク、run_id を応答に（FR-004/SC-002）
  4. **結合確率の性能（HIGH）** → bet_type + 上位 K 限定、無指定で大グリッド返さない（FR-004/SC-003）
  5. **実/推定の判別（HIGH）** → odds_source/is_estimated/coverage_scope/updated_at、混在禁止、二重疑似（FR-005/FR-007/SC-004）
  6. **監査（HIGH）** → run_id/model/logic/computed_at + 結合確率 logic_version（FR-007/SC-002）
  7. **欠損 500 回避（HIGH）** → 404/200空/409-422 の型付き（FR-009）
  8. **セッション寿命（MED）** → アプリスコープ engine/sessionmaker + 読み取り専用セッション（FR-011）
  9. **canonical 母集団（MED）** → 取消・除外を除外+再正規化（FR-012）
  10. **版付け（MED）** → /api/v1 + OpenAPI 自動生成（FR-010/SC-006）
- リーク境界: 応答値をモデル特徴に還流しない（FR-008）。読み取り専用・スキーマ変更なし。新規 `api/` パッケージ + FastAPI 依存。
- analyze 段階の codex 追加 second opinion を反映:
  - **CRITICAL**: read-only は rollback だけでは不十分 → **DB `SET TRANSACTION READ ONLY`**（物理拒否）+ T018 を **AST/import-graph**
    に拡張（commit/flush/add/DML/raw SQL/betting import 全網羅）（FR-011/T003/T018）
  - **HIGH**: win 推奨は selection が dict → `/recommendations` を **exotic 6 券種限定**（list[int] 契約整合）（FR-006/T016）
  - **HIGH**: 全 odds 行に `is_estimated`、estimated に `as_of`、real に updated_at（FR-005/T006）
  - **HIGH**: ページング **NULLS LAST + race_id 全順序**、`total/has_next` は**フィルタ後 COUNT**（T008）
  - **MED**: 予測/オッズの nullable 値は `float|None`（検証エラー回避）（T006）。run 選択は model_versions **JOIN**（T007）。
    estimate_market_odds の **canonical field_size + MarketOddsError 捕捉**（T014）。
