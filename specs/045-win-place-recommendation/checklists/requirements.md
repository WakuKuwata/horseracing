# Specification Quality Checklist: 単勝推奨の製品結線 (045)
**Created**: 2026-07-02 / **Feature**: [spec.md](../spec.md)

## Content Quality
- [x] FR は技術非依存(実装手段は Assumptions/plan に限定)
- [x] Product value 明確(最実用券種の欠落解消)/ stakeholder-readable / 全必須節あり

## Requirement Completeness
- [x] No [NEEDS CLARIFICATION] / testable / SC measurable / AC defined
- [x] Edge cases(selection 形式・冪等細分化・オッズ無・place 不変)
- [x] Scope bounded(place は既存・deferred 明記)/ assumptions あり

## Feature Readiness
- [x] FR↔AC 対応 / primary flow / no schema change

## Notes
- 実査で「複勝は既に EXOTIC 群として結線済み(238行)」と判明しスコープを win に絞った。
- codex CLI 利用不可(companion runtime)— single-opinion。判断: 読み出し正規化(007 契約不変)・win Kelly=016 純関数再利用で含める(real オッズ=Kelly が最も信頼できる券種・画面一貫性)・群単位冪等。
