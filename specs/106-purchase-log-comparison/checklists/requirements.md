# Specification Quality Checklist: 実購入記録と三者比較

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-31
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

- 曖昧さは 3 点あったが、いずれも合理的な既定を置き Assumptions に記録した(clarification
  マーカーは使わず):
  1. 保存場所 → サーバ側(単一オペレータ・ローカル運用の前提。087 予算のローカル保存とは
     性質が違う: あちらは好み、こちらは行動記録)
  2. アプリ外購入の扱い → US4(P3)として含める。P1/P2 だけでも「アプリを使った範囲の
     三者比較」として独立に価値が立つ
  3. 起点と単位 → 記録開始日起点の累積収支(±円)。残高換算は予算がある場合のみ
- FR-011(既存ロジック不変)と FR-003(append-only)は憲法 V/VI の写像
- 「Assumptions のサーバ保存」は plan 段階でスキーマ影響(憲法 VI: 新テーブルの正当化)として
  再検討が必要 — spec 上は保存場所の実装を規定しない
- **clarify セッション(2026-08-31)で 3 点を確定**: 券種スコープ(実購入=全券種実現/政策線=単勝のみ・非対称常設注記+対称ビュー) / 配当欠落時は推定オッズ精算を二重疑似バッジ+常設別掲で算入 / 記録率の分母=全開催レース(閲覧イベント記録は不要に)。チェックリストは全項目 PASS を維持
