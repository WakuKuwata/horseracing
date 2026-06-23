# Specification Quality Checklist: 単勝 EV 推奨と疑似ROIバックテスト

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-06-23
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

- 券種スコープは単勝のみ(ユーザー確定)。結合確率を要する複勝・馬連・三連複と推定オッズ変換は将来(憲法 P0)。
- codex second opinion(リーク境界・疑似評価明示・DNS/DNF/同着の扱い・ROI 専用 baseline・成功基準・selection
  jsonb 形・odds null 除外)を spec に反映済み。最大リスク=closing-oracle backtest を疑似評価と明示しない点 →
  FR-011/SC-006/概要で明示。
- テーブル名・列(recommendations/race_predictions)・`bet_type='win'` 等は Feature 001 のデータ契約語彙(実装技術
  ではない)。prior specs と同方針。
- 成功条件は「baseline を同一条件で上回る」を必要条件とし、`回収率>1.0` は参考バー(SC-007)に分離(控除率考慮)。
