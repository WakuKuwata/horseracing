# Specification Quality Checklist: 低コスト特徴拡充 (030)

**Created**: 2026-06-29 | **Feature**: [spec.md](../spec.md)

## Content Quality
- [x] No implementation details that aren't required by the feature-store contract
- [x] Focused on user value (予測の絶対品質向上)
- [x] All mandatory sections completed

## Requirement Completeness
- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements testable / unambiguous
- [x] Success criteria measurable
- [x] Acceptance scenarios defined
- [x] Edge cases identified (リーク・欠損・市場織り込み)
- [x] Scope bounded (§2 のみ・脚質は §3 送り)
- [x] Dependencies/assumptions identified

## Feature Readiness
- [x] FR に受け入れ基準あり
- [x] User stories が主要フローを網羅
- [x] SC を満たせる
- [x] リーク境界(II)・OOS 採用(III)・スキーマ不変(VI)を明記

## Notes
- 020/023/026 と同じ特徴量ストアの内部契約語を保持（検証可能性のため）。
- **重要なリーク判断**: `running_style` は `corner_orders`(結果)由来と実コード確認 → 今走脚質/展開は本 feature から除外し §3 へ（過去脚質ベースに再設計）。
- codex 設計 second opinion 取得・反映済み: Q1 running_style 結果由来確認→pace_setup §3 送り(✓)、Q2 draw_bias は冗長/市場織り込み→不採用、Q3 斤量 pre-race・馬体重欠損 NaN 伝播、Q4 採用は各 group 独立の事前登録ゲート(group/列/fold/baseline/閾値を eval 前に凍結)、Q5 season 追加・grade はスパース(26.8%)で deferred。
- min_starts(venue/コンビ) は plan で実分布から確定。
