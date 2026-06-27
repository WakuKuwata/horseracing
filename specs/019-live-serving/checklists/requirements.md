# Specification Quality Checklist: ライブ serving（未開催レース）

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

- codex second opinion を反映済み（top-3）: ①発走後/結果存在/部分取得/不正 race_id を fail-closed（FR-001/005/009）、②使用オッズ値 + 対象出走集合を append-only 保存し as_of だけに依存しない（FR-008）、③評価を予測 p のパリティ（過去 odds 再現は前提にしない）と推奨の prospective ログに分離（FR-012/014, US3）。
- codex の重要指摘で spec を補正: races にpost_time が多く null のため cutoff = race_date（004 と同一日付粒度）、「走行済み」判定は結果行不在（result-pending）で行う（壁時計非依存）。同日先行レース混入は 004 から継承の限界として開示。
- 過去 pre-race オッズ非保持のため、推奨/EV の過去パリティは不可（パリティは p のみ）。live Kelly は初期 shadow（実資金なし）。
- スキーマ変更なし（既存 prediction_runs/race_predictions/recommendations + 008 + 016 stake_fraction を再利用）。post_time カラム/自動化/実資金/オッズ履歴は deferred。
- analyze 指摘を解消済み: **F1（MEDIUM）** scrape は URL/DB 状態駆動、race_id→netkeiba URL 自動逆引きは deferred（FR-002 明確化、research R3 / contract / T005）。**F2** 推奨後 scratch の void/skip を T010 で検証。**F3** 生成物が既存 backtest 投入可能を T012 で確認。
