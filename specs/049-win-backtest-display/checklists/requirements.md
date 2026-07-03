# Specification Quality Checklist: win 的中/回収バックテスト表示

**Created**: 2026-07-03 / **Feature**: [spec.md](../spec.md)

## Content Quality
- [X] No implementation details leak into WHAT/WHY(表示要件・境界のみ、内部関数名は plan/tasks 側)
- [X] Focused on user value(推奨が当たったか・いくら回収したかの正直な事後表示)
- [X] Mandatory sections completed

## Requirement Completeness
- [X] No [NEEDS CLARIFICATION] markers
- [X] Requirements testable(FR-001..007、SC-001..005)
- [X] Success criteria measurable
- [X] Scope bounded(win のみ・read-only・schema 不変)
- [X] Dependencies/assumptions identified(044/048 backfill 済み・021/045 前例・codex 不在)

## Feature Readiness
- [X] 各 FR に受入基準(SC)対応
- [X] エッジケース識別(同着 87 レース・stopped DNF・void・null stake 330 件)
- [X] 憲法整合(II leak 境界・V pseudo バッジ不変・VI schema 不変・021 表示規律)

## Notes
- 最大リスク=US2 集計が「儲かる戦略」に見える誤読 → 事実記述限定(n/hit_rate 併記・損益色/ソート/将来射影禁止・retrospective ラベル必須)で緩和。過度に見えるなら US2 は落として US1 のみでも成立。
