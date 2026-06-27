# Specification Quality Checklist: モデル確率校正と edge haircut

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

- 校正手法名（temperature/isotonic/beta）は WHAT を厳密化する仕様であり実装手段の指定ではない（013 spec と同方針、eval で選択）。
- codex second opinion を反映済み（top-3）: ①marginal 校正で joint 改善を前提とせず 009 後の券種別 reliability 非悪化を採用条件に（FR-005/SC-005）、②方式・ハイパラ選択を fold 学習窓内に閉じ小データ fallback を明文化（FR-003/FR-007）、③校正と haircut の役割分離 + 2×2 評価（p×q）と Kelly リスク非悪化ガード（FR-006/FR-012/FR-013/US3）。
- codex の追加助言（採用ゲートは NLL/Brier 主・ECE 補助・Kelly 非悪化必須、overconfidence 指標、両側校正順序）も FR-010/FR-012/FR-013 に反映。
- 多出力/joint 直接校正・オンライン校正・条件別校正・不確実性連動 Kelly は明示的に deferred。
