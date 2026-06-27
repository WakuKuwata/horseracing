# Specification Quality Checklist: モデル改善 — リーク安全な特徴量拡張

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-06-27
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

- LightGBM 等の技術名は初期方針の継続（憲法の技術制約）であり実装手段の新規指定ではない。特徴名は WHAT の厳密化。
- codex second opinion（ML 独立検証）を反映済み（top-3）: ①騎手/調教師フォームは対象行除外・同日除外・out-of-fold を仕様+テストで固定（FR-003/SC-002）、②特徴量選択・ハイパラ選択は各 fold の学習窓内で完結（FR-005/SC-004）、③採用は LogLoss 改善かつ ECE 非悪化を PRIMARY・pseudo-ROI/Kelly は SECONDARY（FR-006/FR-010）。
- codex の追加助言も反映: fold 別差分（勝ち fold 数/最悪 fold/ECE 差分、FR-007）、group ablation（FR-008）、過学習対策（特徴数上限/正則化/安定性、FR-009）、feature spec table + cutoff test（FR-001/SC-001）、効率市場の現実的成功基準＝OOS win 改善（市場超過は努力目標、FR-011）、035/036 前例（片側 fold + 校正未確認）回避。
- ranking/monotonic/model family/multi-output/pedigree/未取得データ/特徴量ストアは明示的に deferred。スキーマ変更なし。
- analyze 指摘を解消済み: **F1（MEDIUM）** 「fold 内特徴選択」を「**候補特徴を事前固定・OOS で特徴を選ばない、fold 内はハイパラ/early-stopping のみ、ablation は diagnostic**」に統一（評価モデル＝デプロイモデル一致、選択リーク原理排除）。FR-005/SC-004/research R4/data-model §5/contract/T009/T010/T013/T014 に反映。**F2** no-schema-change 検証を T017 に追加。
