# Specification Quality Checklist: exotic EV 推奨と疑似ROIバックテスト

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

- 009(P_model on モデル p)× 010(O_est on 市場 win オッズ)= exotic EV。憲法 P0 の 2 つを結実させる。
- codex の BLOCKER/RISK を反映:
  1. **p/q 母集団不一致(BLOCKER)** → 同一 canonical 出走母集団(p と win オッズ両方有効)で 009/010、片方欠損は除外+
     再正規化 or スキップ監査(FR-002/SC-002)
  2. **selection JSONB 安全(BLOCKER)** → 順序券種=順序付き配列/無順序券種=整列配列、**frozenset 非保存**(FR-005/SC-003)
  3. **疑似ROI 採点が単勝専用では不可(BLOCKER)** → 券種別的中判定 + **複勝/ワイドの複数当たりをベット単位**(FR-007/008)
  4. **二重疑似明示** → is_estimated_odds=true, market_odds_used=null, 全評価に二重疑似ラベル(FR-010/SC-006)
  5. **exotic baseline**(最低 O_est/均等)同一条件、成功=baseline 超え(>1.0 ではない)(FR-009)
  6. **組み合わせ爆発** → EV≥閾値 上位 K 制限(FR-003)。p/q 取り違え禁止
- 式・bet_type・selection・控除率は Feature 001/009/010 のドメイン契約語彙(実装技術ではない)。prior specs と同方針。
- リーク境界: 買い目決定は P_model×O_est のみ(結果非参照、FR-004)。決定論・append-only(FR-012)。
