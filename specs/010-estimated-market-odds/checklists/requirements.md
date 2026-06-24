# Specification Quality Checklist: 推定市場オッズ変換

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

- スコープは**推定市場オッズ変換規則そのもの**(憲法 P0)。exotic EV/推奨・推定オッズの永続化・実 exotic オッズ取得は
  将来へ明示分離(憲法 VI の小さく刻む規律)。
- codex の市場モデルレビューを反映:
  1. `q=(1/odds)/Σ(1/odds)` は**市場投票シェア**(真の勝率/モデル p ではない、favorite-longshot bias 含む)— FR-001
  2. 推定単勝オッズ復元は `R·S=1`(控除率=実オーバーラウンド)で厳密成立 — FR-006/SC-001
  3. PL 外挿の推定 exotic オッズは実 exotic 価格と乖離しうる → **疑似評価/「推定」明示** — FR-007
  4. **p と q を別オブジェクト/列で分離**(EV は将来 p×推定オッズ)— FR-008/SC-005
  5. 控除率は JRA 公式既定(時点依存)で**設定可能 + logic_version**、複勝は粗い近似 — FR-003、Edge Cases
  6. 推定確率 0 近傍は**派生オッズを cap、確率本体は cap しない** — FR-005
- 式・q・harville・BetType・控除率は Feature 001/003/009 のドメイン契約語彙(実装技術ではない)。prior specs と同方針。
- 評価先行: 単勝復元誤差 + q 校正(NLL/Brier)、全出力 pseudo 明示(FR-009)。変換は p 非参照(FR-008、リーク境界)。
