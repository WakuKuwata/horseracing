# Specification Quality Checklist: 結合確率エンジン

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

- スコープは**結合確率エンジンそのもの**(憲法 P0)。exotic オッズ取得・推定オッズ変換・exotic EV/推奨は明確に将来へ
  分離(憲法 VI の小さく刻む規律 + 別 P0「推定オッズ変換」)。
- codex の確率レビューを spec に反映: ①ワイドは ordered top-3 列挙の和(独立積 `top3_i×top3_j` 禁止、FR-003)
  ②**再正規化を PL 分母計算より先に**(FR-004)③`harville_topk` の分母 skip を本計算に継承しない・clip+再正規化
  (FR-005)④整合性不変条件(Σ=1・無順序=順序和・周辺=harville・範囲・単調)を必須テスト(FR-006/SC-002)。
- 確率の式・harville_topk・BetType・top-N は Feature 001/003 のドメイン契約語彙(実装技術ではない)。prior specs と同方針。
- 評価先行: 独立積 baseline との校正比較(FR-009)。確率導出は結果/オッズ非参照(FR-008、リーク境界)。
