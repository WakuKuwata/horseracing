# Specification Quality Checklist: 実 exotic オッズ取込と疑似→実 ROI 化

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-06-24
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

- **0001–0004 以降で初の新テーブル追加**(`exotic_odds`、006–011 はスキーマ変更なし)。憲法 VI の正当化は plan に記録(FR-002)。
- codex(codex-rescue)second opinion の BLOCKER/RISK を反映:
  1. **selection キー突合(BLOCKER)** → 011 と同一 `to_selection`/canonical horse_number 配列で完全一致、
     `UNIQUE(race_id, bet_type, selection)` 複合 B-tree(FR-002/FR-008/SC-001/SC-004)
  2. **事前 vs 確定オッズ(BLOCKER)** → codex は `odds_phase`(2 行)を提案したが**憲法 V(スナップショット履歴を持たない・最新値
     上書き)に反する**ため不採用。`race_horses.odds` と同じ**単一最新値 + updated_at**を採用(レース前=事前、確定後=最終配当が
     上書き)。exotic は netkeiba 単独源で JRA-VAN 保護対象なし。決定時オッズは recommendations にスナップショットして監査(FR-004/SC-003)
  3. **冪等/監査(BLOCKER)** → `ingestion_jobs.job_type='exotic_odds'`、status=partial、summary に期待/観測/欠損、UNIQUE で dedup
     (FR-005/SC-001)
  4. **実 vs 推定フォールバック配線(BLOCKER)** → canonical 母集団・to_selection を必ず経由、行単位で実/推定を区別、推奨後取消は
     void/skip(FR-007/FR-008/FR-009/SC-004/SC-005)
  5. **三連単グリッド量(RISK)** → coverage_scope(full/partial)、完全は期待件数テストで証明、欠損は推定フォールバック(既定方針を
     Assumptions に明記)
  6. **乖離評価設計(RISK)** → カバレッジ率・符号付き log(実/推定)・中央値/MAE/P90、推定=baseline、実/推定ラベル分離(FR-010/SC-006)
  7. **憲法ゲート(RISK)** → II リーク(FR-006/SC-007)・III 評価先行(US3/FR-010)・V 監査(FR-005/FR-011)・VI 分割(FR-013)を spec に内在
- リーク境界: exotic オッズはモデル特徴にしない(win オッズと同一、FR-006)。取込・突合・評価は決定論(FR-011)。
- カバレッジ方針は既定値(全公開グリッド格納 + coverage_scope + 推定フォールバック)を採用し Assumptions に記録。完全カバレッジ保証は将来。
