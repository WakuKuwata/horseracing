# Specification Quality Checklist: 馬体重欠損時の serving 入力是正

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-10
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

### 検証で確認した事実(推測でなく実査)

- `FEATURE_VERSION` の現行値は `features-018`([registry.py:368](../../../features/src/horseracing_features/registry.py))。FR-017 の `features-020` は正しい次番号。
- `features-019` は 070 の revert で撤去済みであり、[registry.py:408-409](../../../features/src/horseracing_features/registry.py) に「No model was ever trained on features-019」と明記されている。再利用禁止(FR-017)の根拠は実在する。
- FR-018 が援用する版固定の互換機構 `COMPATIBLE_PRIOR_FEATURE_VERSIONS` は実在し、現行は `features-018 → features-017` の pin を保持している。
- 背景に記載した欠損率・kill-test 数値・体重データ品質はすべて実 DB での実測であり、再現スクリプトと JSON レポートを [`evidence/`](../evidence/) に同梱した。

### 意図的な判断

- **コード参照の扱い**: 「背景と問題」および本 Notes には現行実装のファイル・行番号を引用している。これは主張の裏取りを可能にするための**証跡**であり、設計指示ではない。Requirements / Success Criteria の各項目は列名・関数名・ファイル名を含まず、振る舞いのみで記述してある。この使い分けは 087 など既存 spec の慣行と一致する。
- **閾値の一部が plan 送り**: FR-011(学習時の欠損付与率)と SC-002(非劣化幅)は具体数値を spec に固定していない。いずれも「評価開始前に凍結し記録する」ことを FR-011 / FR-025 で要求しており、検証可能性は担保されている。数値そのものは plan / gate-config で事前登録する。
- **[NEEDS CLARIFICATION] を立てなかった理由**: 設計上の主要な分岐(独立列方式 vs 既存列への混入、休養明けの上限、前走体重の母集団定義、評価 regime)はすべて kill-test の実測と codex レビューで決着済み。残る未定事項は数値の事前登録のみで、これは plan フェーズの仕事である。

### 残リスク(plan で扱う)

- 独立列方式そのものは未測定である(kill-test は既存列への混入方式を測った)。spec の Assumptions に明記済みで、採否は本 feature のゲートで判定する。
- serving regime での評価は、この repo の既存 paired-eval に無い評価条件である。ゲート実装が plan の主要な設計対象になる。
