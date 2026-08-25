# Specification Quality Checklist: 評価契約 v5

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-25
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain — Q1(δ の出所)は**多重検定予算からの導出**で確定(FR-030/030a)
- [x] 当初仮説のうち実測で否定された部分が、削除でなく**否定された記録として**残っている
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

- [x] **自己欺瞞への防御がある**: 分散削減は常に「効いたように見える」。FR-018/019/020 が実データの CI 幅を成功の証拠にすることを禁じ、合成データでの偽陽性率と被覆率を必須検証にしている。
- [x] **生き残った US にも同じ足切りが掛かっている**: US2 を測定で殺した以上、US3 だけ「理屈で効くはず」で通すのは二重基準。FR-016 がスパイク足切りを課している。
- [x] **null が成功として定義されている**: US2 は spec を書いた当日に T0 測定で棄却され、その記録が spec 本体に残っている(FR-013/014)。予測を書いてから測るのでなく、**測ってから spec を確定した**。
- [x] **既存の設計判断を否定していない**: 「k-seed 平均は割に合わない」という凍結コメントを実数で追認したうえで、その前提(出荷物が単一 seed)を明示して別経路を提案している。
- [x] **過去 verdict の不可侵が守られている**: FR-004/031/034、SC-008。
- [x] **後方互換が要件になっている**: FR-002/003、SC-002。

## Notes

- Q1 は解決済み。全項目 PASS。`/speckit-plan` に進める。
