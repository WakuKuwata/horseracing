# Implementation Plan: 市場との関係とオッズの鮮度を正直に出す

**Branch**: `feat/103-market-first-honesty` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/103-market-first-honesty/spec.md`

---

## Summary

**表示層だけの変更。** 測定も新データも新モデルも要らない。**既に測って分かっていることを、
画面が言っていないので言わせる。**

| US | 内容 | 規模 |
|---|---|---|
| **US1** | モデルと市場の関係を常時開示(勝率モデルと荒れ度を分けて) | 小(新規コンポーネント 1) |
| **US2** | オッズの鮮度と派生指標の依存を明示 | 小(新規コンポーネント 1) |

**先に確認したこと**: 画面は既にかなり市場中心だった(列順は市場が先・乖離は中立語彙で
ソート不可・荒れ度も出ている)。当初「市場中心に寄せる」と提案したが**その大半は実装済み**で、
実際の穴は上の 2 つだけだった。

**codex レビューで私の文言案が 2 箇所間違っていた**ことも反映済み(「順位付けの精度」は
LogLoss の実測と不一致 / 「違う読みだから価値がある」は**未実測の主張**)。

---

## Technical Context

**Language/Version**: TypeScript 5 / React 18 / Vite

**Primary Dependencies**: 既存のみ。**新規依存ゼロ。**

**Storage**: N/A(read-only 表示)

**Testing**: Vitest + React Testing Library + MSW

**Target Platform**: ブラウザ(`front/` SPA・dev は 127.0.0.1)

**Project Type**: read-only SPA(既存 014 API を消費)

**Performance Goals**: 追加のネットワーク往復ゼロ。必要な値
(`odds_as_of` / `post_time`)は**既に応答に含まれている**。

**Constraints**:
- **API / OpenAPI / DB / モデル / 買い目生成を変更しない**
- 既存の表示規律を壊さない(pseudo バッジ・read-only・`canonical_consistent` による抑制)
- **未測定の量を表示しない**(オッズ変動幅・方向・目安)

**Scale/Scope**: `front/src/` のみ。新規コンポーネント 2 + `RaceDetailPage` への結線。

---

## Constitution Check

- [x] **I. データ契約**: **N/A**。ID もラベルも触らない。
- [x] **II. リーク防止**: **PASS**。表示専用で、**表示派生値をモデル特徴に戻さない**。
  新しい計算を一切行わない(既存の応答値を並べ替えて見せるだけ)。
- [x] **III. 評価先行**: **N/A**。モデルも特徴も変えないので採否判定の対象ではない。
  **表示する事実はすべて既に測ってある**(047 / 086 / 064 / 084)。
- [x] **IV. 確率整合性**: **PASS**。確率に触れない。
- [x] **V. 再現性・監査**: **PASS**。pseudo バッジと `odds_as_of` の扱いを強化する方向で、
  弱める変更はない。**未測定の量を出さない**ことを FR-005b で明文化した。
- [x] **VI. feature 分割規律**: **PASS**。API 契約は不変(`odds_as_of` / `post_time` は既存)。
  OpenAPI の drift-check が緑であることを受入に含める。
- [x] **品質ゲート**: **PASS**。codex 設計レビュー取得済み・**採用 9 / 不採用 0**
  ([codex-review.md](./codex-review.md))。

**違反ゼロ** → Complexity Tracking 不要。

---

## Project Structure

```text
specs/103-market-first-honesty/
├── spec.md            # 完了(FR-001..008 + 002a/002b/005a/005b)
├── plan.md            # 本ファイル
├── codex-review.md    # 完了(採用 9 / 不採用 0)
├── checklists/
└── tasks.md           # Phase 2

front/src/components/
├── ModelMarketStanding.tsx   # 新規: US1(勝率は市場が上・荒れ度は識別力あり)
├── OddsFreshness.tsx         # 新規: US2(取得時刻・残り時間・派生指標の依存)
└── HorseEntriesTable.tsx     # 既存: 「市場との差」セルの注記に 1 文追加

front/src/pages/
└── RaceDetailPage.tsx        # 結線(出走表の直上に常時表示)
```

**Structure Decision**: `front/` に閉じる。既存コンポーネントへの変更は
`HorseEntriesTable` の注記 1 文だけで、**表の構造・列・ソートは触らない**。

---

## 設計判断

1. **置き場所は出走表の直上・レース単位**(codex R5)。全セグメント共通の検証結果であって
   特定馬の評価ではないので、馬単位に置くと意味がずれる。
2. **勝率モデルと荒れ度を分けて書く**(codex R3)。一緒くたにすると片方の評価が
   もう片方に漏れる。勝率は市場に劣り、荒れ度には識別力がある。
3. **常時表示**。折りたたみや tooltip だけにしない(FR-001)。
   089 の教訓 — 折りたたみの中は「表示した」ことにならない。
4. **未測定の量を出さない**(FR-005b)。オッズの変動幅・方向・「通常 ○% 動く」は
   **一度も測っていない**。目安として出すのはこの製品が禁じている型である。
5. **禁止語テストを拡張する**。既存の front には `RaceDivergenceSummary` / `RaceDispersionPanel` /
   `RaceChaosPanel` に禁止語テストの前例がある。**codex が挙げた 5 種を足す**。

---

## Phase 構成

```
Phase 1: US1(モデルと市場の関係)  ← 単独で価値が出る
Phase 2: US2(オッズの鮮度)
Phase 3: Polish(禁止語テスト拡張・drift-check・実画面確認)
```

中断点は置かない — どちらも小さく、測定に依存しないので「やってみたら効かなかった」が無い。

---

## Complexity Tracking

Constitution 違反なし。記入不要。
