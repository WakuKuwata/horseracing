# Specification Quality Checklist: RaceFront（閲覧専用 React/Vite フロント）

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-06-26
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

- 憲法 VI（契約先行）: 014 で API/DB 契約を確定済み → 本フィーチャーで画面化。OpenAPI が契約。
- codex(codex-rescue) second opinion の CRITICAL/HIGH を反映:
  1. **疑似ラベル強制（CRITICAL）** → 疑似値（推定オッズ/pseudo_odds/pseudo_roi/double_pseudo）はラベル/バッジ無しで表示しない。
     判別ユニオン型 + コンポーネント + **不変条件テスト**で担保（FR-006/SC-005）
  2. **OpenAPI 型同期（HIGH）** → コミット済み OpenAPI スナップショット + 生成型、ドリフト検知、起動 API から再生成スクリプト（FR-009/SC-006）
  3. **null 数値（HIGH）** → 整形前 null ガード、`--`/`未提供` 安全表示（FR-008/SC-006）
  4. **3 状態区別（HIGH）** → loading/空(200 typed-empty)/エラー(型付き本体) を別表示（FR-007/SC-001）
  5. **監査可視化（HIGH）** → run_id/model/logic/computed_at/as_of を画面明示（FR-003/SC-002）
  6. **CORS/dev-proxy（MED）** → 相対 `/api/v1/*` + Vite proxy、014 は CORS 無しのまま変更しない（FR-010/SC-007）
  7. **ページングは一覧のみ（MED）** → 詳細サブリソースはフラット配列（FR-002 注記）
  8. **テスト方針（HIGH）** → RTL + モックで full/空/エラー/null/ページング/疑似ラベル不変条件（FR-011）
  9. **憲法 UI リスク（HIGH）** → 判別ユニオンでラベルをコンパイラ強制（V）、スナップショット検査でドリフト防止（VI）、書込 UI 無し（II）
- 閲覧専用・API 非変更（CORS 無しのまま）。新規 `front/` パッケージ + React/Vite/TS 依存。
- analyze 段階の codex 追加 second opinion を反映:
  - **CRITICAL**: 疑似不変条件はスポットでなく**カバレッジ** → `data-pseudo` 標識 + `assertPseudoLabelCoverage`（T007/T010、US3/US4 で使用）
  - **HIGH**: `/odds` の exotic 推定は `?bet_type=` 指定時のみ返る → 券種セレクタで再フェッチ（T018/T019）
  - **HIGH**: FR-011 = 各エンドポイント full/空/**エラー**分岐を網羅（T020/T023 にエラー追加）
  - **HIGH**: MSW v2 ライフサイクル + `renderWithProviders`（新規 QueryClient/テスト）+ user-event（T002）
  - **MED**: drift 検査の決定論（openapi-typescript 厳密ピン + packageManager + 整列出力 + 版 assert、T001/T024）
  - **MED**: `parseApiError` が ErrorBody と FastAPI `{detail:[...]}` 両形を受理（T005）
  - **LOW**: 将来面ガード（auth/Kelly/Playwright/deploy/書込）静的走査（T026）。`prediction_run_id` 表記統一（T015）
