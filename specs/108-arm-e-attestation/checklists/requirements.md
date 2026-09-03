# Specification Quality Checklist: arm E 系モデルの OOF attestation 対応

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-02
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs) — ファイル名・クラス名・関数名を
  本文から排除。背景表の 4 ブロッカは「何ができないか」を機能の言葉で記述(コード位置は plan の領分)
- [x] Focused on user value and business needs — 利用者=運用者。**価値が小さいことを冒頭で開示**した
  うえで、やる理由(基盤が恒久に不活性)を提示
- [x] Written for non-technical stakeholders — 背景表は技術的だが、各行は「現状/実態/帰結」の
  3 列で読める。校正・OOF は Key Entities で定義
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain — ゼロ。入力記述が完全で、曖昧点は repo 前例
  (076 の「既定 ON は別判断」・091/100 の中断点)で既定が定まる
- [x] Requirements are testable and unambiguous — FR-001..018 各々が検査可能
- [x] Success criteria are measurable — SC-001..008(一致/ゼロ/1 つ/3 種すべて など計数可能)
- [x] Success criteria are technology-agnostic — attestation/manifest/verdict は Key Entities で
  定義した領域概念。特定の言語・framework・API に言及なし
- [x] All acceptance scenarios are defined — US1:5 / US2:4 / US3:4
- [x] Edge cases are identified — 記録と実物の不一致・実行コスト・重み mask 再現・旧世代互換・
  両 verdict 非採用
- [x] Scope is clearly bounded — 既定切替は範囲外(FR-015)・スキーマ/API 不変(FR-016)・
  win バイト不変(FR-017)
- [x] Dependencies and assumptions identified — Assumptions 6 件

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria — 各 FR が US の受入シナリオ
  または SC に対応
- [x] User scenarios cover primary flows — 記録・再構成(US1)→ 測定・凍結(US2)→ 活性化検証(US3)
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## analyze 後の再検証(2026-09-02)

- [x] **背景記述が実測と一致**(analyze I1 で「即 reject」が誤りと判明 → 「捏造して通る
  silent fail-open」に全面訂正・実行して確認済み)
- [x] **設計が実装可能**(analyze U1 で当初の再構成設計が `ModelRecipe(split_unit=None)` の
  fail-closed により構築不能と判明 → 出荷ビューと構成ブロックの分離に是正)
- [x] **変更範囲が正確**(analyze G1 で `oof_generate` の旧世代強制が判明 → plan の
  「1 ファイルに集中」を 4 ファイルに是正・T011a を新設)
- [x] **保証の主張が過大でない**(登録 recipe hash 未保存のため「識別子の同一性」を撤回し
  「挙動的一致」+ 保証境界の明記に是正・codex 指摘 3)
- [x] **見積の算術が整合**(171 × 112 秒 = 5.32h → 「5〜8 時間」・停止閾値 12 時間に再導出)
- [x] codex 設計レビュー取得成功(採用 6 / 部分採用 1 / 不採用 1)

## Notes

- **null verdict も成功**(FR-010): 校正 verdict は測定結果であり、両 stage 非採用でも
  「実行時 fit をやめて凍結値を読む」という監査上の達成は残る
- **中断点が要件に入っている**(FR-018): OOF 再生成の実コストを 1 fold 実測で確定してから
  本実行に進む。plan でこの中断点をフェーズ境界に置くこと
- Edge case「記録と実物の不一致」は research D4 で**決着済み**(出荷関数が 1 フィールドを
  上書きし忘れた残留値・真値は構造的に非退化)
- **analyze が CRITICAL 3 件を検出し、いずれも「着手すれば必ず止まる」種類だった**
  (G1: 旧世代強制で例外 / U1: recipe が構築不能 / I1: 根本原因の記述が事実と逆)。
  実装前に回した価値が最も出た回
