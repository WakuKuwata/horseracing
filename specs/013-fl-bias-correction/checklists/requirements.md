# Specification Quality Checklist: 人気-不人気バイアス補正

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

- 010 が明示的に deferred した FL バイアス補正を実装。012 の乖離ハーネスを評価ツールに再利用（評価先行の結実）。
- 設計上の要点（plan/analyze で codex と突合予定）:
  1. **リーク境界**: q→q' の学習は実現勝敗を使うが、これは**市場オッズ側の変換**でありモデル特徴ではない（odds/q' は win
     モデルに戻さない、FR-003）。walk-forward で評価対象レースの結果を学習に使わない（FR-001）。
  2. **per-horse 単調写像 → Σ=1 再正規化**: 単調 f を各馬に適用後レース内再正規化（順序保持、009 へ正規化ベクトル、FR-002）。
  3. **オッズ復元の非保存**: 補正後は生オッズを厳密復元しない（バイアス除去の意図、FR-006/SC-003）— 010 SC-001 との差分を明示。
  4. **評価ターゲット**: 主指標=実現勝率校正（直接の真値、FR-007）、乖離=補助（実 exotic は独自の偏り、FR-008）— 偏った
     ターゲットへの過剰最適化を回避。
  5. **方式**: isotonic（単調保証）/ パラメトリック（外挿）、設定可能 + 再現メタ（FR-004）。小サンプル帯はサンプル数明示（FR-010）。
- 憲法ゲート: II リーク（FR-001/003/SC-002）・III 評価先行（US3/FR-007/008）・IV 整合性（FR-002）・V 監査（FR-004/009）・VI 分割（FR-012）。
- スキーマ変更なし。p≠q を厳守、オッズは特徴量にしない。
