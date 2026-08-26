# Specification Quality Checklist: recency weighting

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-26
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain — FR-007(半減期の選び方)は codex R2 により **日付だけを使った事前登録**で spec 段階で確定した
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## この spec 固有の追加検証

- [x] **null が成功として定義されている**: Assumptions が「成功条件は効くことではなく一度で決着させること」と明記。
- [x] **要求水準が正直に書かれている**: 採用には −0.0025 かつ δ=0.00352 超が要り、それは過去に効いたレバーの帯だと明記(小さい効果では通らないことを隠していない)。
- [x] **理屈で通さない規律が引き継がれている**: FR-015 が「一貫適用か booster 限定かは測定で決める」と明記(feature 100 の US2 が理屈のまま載って棄却された前例を踏まえる)。
- [x] **リーク面がゼロであることが構造的に言える**: 重みは (race_date, cutoff) の純関数(FR-002)。
- [x] **既存の fail-closed 機構に載る**: レース内定数の検証(FR-003)、arm E のフィールド宣言強制(FR-005)。
- [x] **交絡が最初から仕様に入っている**: US3 が「時間変化か供給元切替か」を切り分ける。効いた場合に原因を取り違えない。
- [x] **codex レビュー** — 取得済み・反映済み(採用 10 / 部分採用 1 / 不採用 1)。[codex-review.md](../codex-review.md)
- [x] **正則化との混同を潰してある**: FR-006a がレース平均 1 への正規化を非交渉にしている(重み総量が lambda・leaf 条件・early stopping を動かすため)
- [x] **識別限界が明記されている**: FR-017b が「完全な識別はできない・並行取得が無い」と書いている

## Notes

- 全項目 PASS。`/speckit-plan` に進める。
- plan で決めること: 重みの適用範囲(booster 限定 / 一貫適用)を**両方測る**設計と、
  FR-007 の日付基準の具体値(質量 20〜35% のどこを狙うか)。
