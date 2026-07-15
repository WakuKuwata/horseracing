# Specification Quality Checklist: Evaluation Contract Correctness

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-15
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

- ドメイン上「ユーザー」は研究者/オペレータであり、評価契約という内部基盤のため技術用語(split, ECE, bootstrap, calibrator, serving)は不可避的に登場する。これらは実装技術ではなく評価ドメインの語彙として扱う。
- **codex 設計レビュー反映済み(2026-07-15)**: 当初の単一スコープ案から 3 feature に分割(073 契約修正+凍結 / 074 校正 artifact 是正 / 075 API 命名 migration)。codex の主要指摘=(1)校正リーク是正は artifact 凍結でなく OOF-faithful 作り直し=074 へ / (2)realized 改名は公開 API 破壊的変更=075 へ / (3)split は recipe 意味論化+legacy 凍結にとどめ再学習は別 feature / (4)gate は三値単一 enum / (5)bootstrap 改名+過去 verdict 凍結。レビュー全文は `docs/plan/codex-075-review.md` に保全。
- 最大の設計判断(split の適用範囲・既存 active parity)は FR-009〜012 と Assumptions に不変条件として固定済み。plan で recipe 意味論化の実装を確定する。
- 現 active version(062 か 063 か)は着手時に実 DB で確定する(推測固定しない)。
