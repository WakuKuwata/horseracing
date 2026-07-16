# Specification Quality Checklist: OOF-faithful Calibration Evidence

**Purpose**: Validate specification completeness and quality before planning
**Created**: 2026-07-16
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details beyond the eval/calibration domain vocabulary
- [x] Focused on the correctness value (fix the calibration leak) and honest evidence
- [x] Written for the researcher/operator stakeholder
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic where possible (parity/leak/determinism framed as outcomes)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded (evidence-only; activation=076, registry=077)
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No production-wiring leaks into this spec (FR-015)

## Notes

- **codex 設計レビュー反映済み(2026-07-16、`docs/plan/codex-074-review.md`)**: 主要指摘=(1)persisted-run 再利用は不可(過去 prediction は full-history=非OOS)→ OOF bundle は fold ごと再学習の content-addressed disk artifact / (2)serving parity は「win byte 不変・表示 top2/top3 は新 run で変更可・既存 run 不変」に線引き / (3)manifest は 3-file freeze の単純拡張では不足(metadata checksum・full 精度 γ/λ・fold race hash・OOF checksum 等) / (4)3分割: 074 evidence-only・076 activation・077 registry(最小 OOF manifest は 074 に残す) / (5)073 FR-007 は 074 の append-only artifact への参照で満たす。
- 現 `ModelRecipe` は完全再現 recipe でない(US2 の legacy attestation で補完)。
- 計算コスト高(fold ごと再学習)= 長時間 job 前提を Assumptions に明記。
- 着手時に 073 tasks.md の ECE-subset/audit-hash が genuine に未完(074 前提)であることを確認済み。
