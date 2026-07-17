# Specification Quality Checklist: Counterfactual Return API Terminology

**Purpose**: Validate specification completeness and quality before planning
**Created**: 2026-07-16
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details beyond the necessary API/field vocabulary (this is a naming contract feature)
- [x] Focused on the honest-labeling value (counterfactual vs realized vs current)
- [x] Written for the viewer/researcher stakeholder
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic where possible (naming/parity/drift framed as outcomes)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded (win backtest/shadow-log + favorite provenance; NOT calibration realized_rate, NOT exotic, NOT numeric changes)
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No numeric/logic change leaks into this naming migration (FR-007)

## Notes

- 073 で予約・074 codex レビューで命名確定済み(gross/net/recovery/valuation_basis/current_odds provenance)。
- 最重要の区別(改名する counterfactual snapshot vs 改名しない empirical realized_rate vs 別ラベルの current_odds)は US1/US2/US3 と FR-001/005/006 に明記。
- 破壊的変更(後方互換なし)= plan/tasks で front/admin の原子同期(openapi snapshot 再生成 + drift-check)を厳密に順序付ける。
- codex 設計レビューは plan 段で取得予定(API 契約変更=MUST)。ただし本セッションで codex は repo skill に derail する既知問題があり、失敗時はセルフレビューで代替。
