# Specification Quality Checklist: Harville stage 割引 — top2/top3 校正改善

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-02
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs) — 手法定義(λ・分母)は事前登録対象の統計仕様であり実装詳細ではない。013/017/048 の既存 spec と同水準。
- [x] Focused on user value and business needs — 表示中の連対率・複勝率と複勝/ワイド系 EV の正直さ(製品目的=意思決定支援)に直結。
- [x] Written for non-technical stakeholders — 実測テーブルとずれの pt 表示で非技術者にも判読可能。
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous — バイト一致・不変量・厳密前境界・ゲート数値が全て機械検証可能。
- [x] Success criteria are measurable — ECE/LogLoss/乖離 pt/テスト緑/E2E。
- [x] Success criteria are technology-agnostic — 指標と結果のみ。
- [x] All acceptance scenarios are defined — US1〜US3 各々に Given/When/Then。
- [x] Edge cases are identified — 同着・少頭数・eps・境界張り付き・サンプル不足・取消。
- [x] Scope is clearly bounded — 診断は完了済みでスコープ外、q 側・条件別 λ・代替関数形は deferred、048 との層分離を明記。
- [x] Dependencies and assumptions identified — 既存ハーネス流用・フィットインフラ流用・048 との合成順。

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows — 導出+フィット→採否→製品結線。
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- codex second opinion は CLI 起動不可(本セッション確認)のため single-opinion。文献裏付け(Henery/Stern/Benter)と 013/017/048 前例で補強。plan フェーズで再試行余地あり。
- 事前登録値: λ∈[0.1,5.0]・min_races=300・PRIMARY/MUST/ガードは spec 記載のとおり実行前固定。
