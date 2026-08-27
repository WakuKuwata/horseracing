---
description: "Task list for 103-market-first-honesty"
---

# Tasks: 市場との関係とオッズの鮮度を正直に出す

**Input**: `specs/103-market-first-honesty/`(spec / plan / codex-review)

**Tests**: **含む。** 表示の誠実さは**テストでしか守れない**(禁止語・未測定量の非表示・
常時表示であること)。front には既に禁止語テストの前例がある
(`RaceDivergenceSummary` / `RaceDispersionPanel` / `RaceChaosPanel`)。

**Organization**: US ごと。中断点なし(測定に依存しないので「やったら効かなかった」が無い)。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 `front/src/lib/forbiddenPhrases.ts` に禁止語の共有定数を切り出す(テストは
      colocated `*.test.tsx` の慣習)。**スコープを厳密に決める**(analyze C3):
      - **正本にするのは `RaceDivergenceSummary` と `RaceDispersionPanel:103` のパターンだけ**
        (`妙味|危険|儲|回収率|edge|買うべき|勝てる|おすすめ|お得`)
      - **`RaceChaosPanel` のパターンは合流させない。** そこには `利益` `暫定` `EV 中立` が
        含まれており、**本 feature の採用文言そのもの**(「…**利益**機会とは解釈せず」)が
        引っかかる。荒れ度パネル固有の規約として据え置く
      - **`RaceDispersionPanel:142` の狭いパターン(裸の `買` を含む)も据え置く** —
        統合すると裸の `買` が全体に波及して誤検出する
      - **codex が挙げた 5 種**を追加(「順位付けでは市場が上」「市場が見落とし」
        「市場より先」「違う読みだから価値」「モデルは使えない」)

---

## Phase 2: US1 — モデルと市場の関係を常時開示 (Priority: P1) 🎯 MVP

**Goal**: 利用者が、モデル勝率と市場評価が**どういう関係にあるか**を知った上で読める。

**Independent Test**: レース詳細を開き、出走表の直上に関係が常時表示されていれば完了。

### テスト(先に書く)

- [X] T002 [US1] `front/src/components/ModelMarketStanding.test.tsx` を追加し、
      **勝率モデルと荒れ度が分けて書かれている**ことを検証する(FR-002b)。
      片方の評価がもう片方に漏れていない
- [X] T003 [US1] 同テストに**禁止表現 5 種が 1 つも出ない**ことを追加する(FR-002a)。
      特に「**違う読みだから価値がある**」— 乖離の有用性は未実測なので書いてはならない
- [X] T004 [US1] 同テストに、**折りたたみの中ではなく常時表示**であることを検証する
      (`<details>` の中に入れると「表示した」ことにならない・089 の教訓)

### 実装

- [X] T005 [US1] `front/src/components/ModelMarketStanding.tsx` を新規作成する。
      **数値の出所をコード内コメントに残す**(047 / 2021+ / n=181,341)— 画面には出さない。
      既存注記の `(020)` は帰属が誤ったまま画面に出ていた前例なので、**コードに正しい出所を
      固定する**(analyze C11)。文言は
      spec の採用文(codex 推奨をほぼそのまま):
      「勝率予測では市場評価がモデルを上回っています(検証データ 181,341 件・すべての検証
      セグメント)。一方、荒れ度の分類には実測上の識別力があります。モデル勝率との差を
      利益機会とは解釈せず、レース傾向と見送り判断の補助情報としてご覧ください。」
- [X] T006 [US1] `front/src/pages/RaceDetailPage.tsx` の**出走表の直上**(`RaceChaosPanel` の
      手前)に結線する。ゲートは **`canonicalConsistent === true`**(表側の `comparable` と同一)。
      `!== false` だと **null(オッズ無し)で通ってしまう**(analyze C2)
- [X] T006a [US1] **旧注記を削除する**(analyze C1)。`RaceDetailPage.tsx` の
      `data-testid="market-superiority-note"` の `<p>` を除去する。訂正版と**二重に出さない**。
      既存テストがこの testid を参照していれば新コンポーネント側に付け替える
- [X] T007 [US1] **判断: 変更なし。** `DIVERGENCE_TOOLTIP` は既に「的中や利益を保証するものではありません」と書いており codex R6 を満たす(重複を増やさない)。参考: `HorseEntriesTable.tsx` の `DIVERGENCE_NOTE`(現行「モデル勝率と市場評価の
      差です。意見の相違であり、的中や**利益**を保証するものではありません」)が codex R6 の
      要求を**既に満たしているか判断する**。満たしていれば**何もしない**(重複を増やさない・
      analyze C9)。満たしていなければ列見出しの `title` 属性に 1 文追加する。
      **行番号ではなく属性名で指す**(行はずれる)。表の構造・列・ソートは触らない

---

## Phase 3: US2 — オッズの鮮度と派生指標の依存 (Priority: P2)

**Goal**: 見ているオッズが**いつのもので**、**まだ動く**ことと、**何がそれに依存しているか**が分かる。

**Independent Test**: 取得時刻・残り時間・「最終オッズではない」・派生指標の依存が読めれば完了。

### テスト

- [X] T008 [US2] `front/src/components/OddsFreshness.test.tsx` を追加し、
      **絶対時刻と相対時刻が併記**されることを検証する(FR-004/005)
- [X] T009 [US2] 同テストに、**`post_time` が無いとき残り時間を出さず**
      「発走時刻未登録のため残り時間は表示できません」と出ることを検証する(推測で埋めない)
- [X] T010 [US2] 同テストに、**`odds_as_of` が無いとき「不明」**と出ることを検証する
- [X] T011 [US2] 同テストに、**未測定の量が 1 つも出ない**ことを検証する(FR-005b):
      変動幅・変動方向・「通常 ○% 動く」・秒単位カウントダウン・「新鮮/古い」判定。
      **オッズの変動分布は一度も測っていない**ので目安として出せない
- [X] T012 [US2] 同テストに、**結果確定済みのレースでは「まだ動く」を出さない**ことを
      検証する(FR-006)

### 実装

- [X] T013 [US2] `front/src/components/OddsFreshness.tsx` を新規作成する。**props で出所を
      明示的に受け取る**(analyze C4/C6): `oddsAsOf`(**`PredictionResponse`** 由来)/
      `postTime`・`hasResults`(**`RaceDetail`** 由来)。**別クエリから来る**ので暗黙に
      取りに行かない。`hasResults` が FR-006(確定済みなら「まだ動く」を出さない)の入力。出すのは
      `YYYY/MM/DD HH:mm 時点のオッズ(発走約 N 時間前)` / 「最終オッズではなく、発走までに
      動く可能性があります」/ **「市場評価・EV・疑似 ROI は、この時点のオッズを使って
      計算しています」**(FR-005a・依存関係の明示)
- [X] T012a [P] [US2] `OddsFreshness.test.tsx` に **FR-005a の依存明示**
      (「市場評価・EV・疑似 ROI は、この時点のオッズを使って計算しています」)が
      レンダリングされることを検証する(analyze C5 — codex が「tooltip 1 箇所では不十分」と
      名指しした唯一の節なのにテストが無かった)
- [X] T012b [P] [US2] `OddsFreshness.test.tsx` に**禁止語テスト**を足す(analyze C7 —
      US2 側だけ禁止語検証が無かった)。併せて**損益色クラスとソート属性が付いていない**ことを
      `RaceDispersionPanel.test.tsx:101` と同じ形で検証する(FR-003)
- [X] T004a [P] [US1] `ModelMarketStanding.test.tsx` に、**`canonical_consistent` が
      `false` / `null` のとき表示されない**ことを検証する(analyze C8・FR-008a)
- [X] T014 [US2] `front/src/pages/RaceDetailPage.tsx` に結線する。US1 の直下、出走表の直上。
      レース単位で 1 回だけ出す(**各セルへの時刻反復はしない**・codex R9)。
      **既存の監査行(`オッズ時刻: <code>`)は残す** — provenance の生値として役割が違う。
      US2 が人間可読の正本、監査行が生値(FR-010・analyze C10)

---

## Phase 4: Polish

- [X] T015 [P] `RaceDivergenceSummary.test.tsx` と `RaceDispersionPanel.test.tsx` の **103 行目の
      パターンだけ**を T001 の共有定数に寄せる。**142 行目の狭いパターンと `RaceChaosPanel` は
      触らない**(T001 のスコープ判断に従う・analyze C3)
- [X] T016 [P] `cd front && pnpm test` が緑・`pnpm tsc --noEmit` と `pnpm lint` がクリーン
- [X] T017 [P] `cd front && pnpm check:openapi`(drift-check)が緑 =
      **API 契約に触れていない**ことの機械的な確認(SC-004)
- [X] T018 `git diff --stat` で `api/ db/ betting/ probability/ serving/ training/ features/ ops/`
      に**差分ゼロ**を確認する(SC-004・spec 側の列挙と一致させた)
- [X] T019 実画面で確認する。`scripts/stack.sh start` でスタックを起動し、予測のあるレースと
      無いレース、`post_time` のあるレース(2026 年)と無いレース(2024 年以前)の
      **4 通り**を開いて表示を確かめる
- [ ] T020 `CLAUDE.md` の SPECKIT 区間を更新する(**speckit の agent-context 更新スクリプトは
      使わず Edit で手動編集**)
- [ ] T021 変更をパスを明示列挙してコミットする(`git add -A` 禁止)

---

## Dependencies

```
Phase 1 (禁止語の共有定数)
   ↓
   ├─→ Phase 2 (US1) ──┐
   └─→ Phase 3 (US2) ──┴─→ Phase 4 (Polish)
```

**US1 と US2 は互いに独立ではない**(analyze C4)。どちらも `PredictionResponse` に依存する
(US1 は `canonical_consistent`、US2 は `odds_as_of`)。ただし**触るファイルは別**なので
実装は並列に進められる。予測が無いレースでは**両方とも出ない/退化する**ことに注意。

---

## Independent Test Criteria

| US | 単独で完了と言える条件 |
|---|---|
| **US1** | 出走表の直上に関係が常時表示され(T004)、勝率と荒れ度が分かれており(T002)、禁止表現が 1 つも無い(T003) |
| **US2** | 絶対+相対時刻が併記され(T008)、欠損時に推測で埋めず(T009/T010)、未測定の量を出さない(T011) |

---

## Implementation Strategy

**MVP = Phase 1 + Phase 2(US1)。**

US1 だけで「測って分かっていることを画面が言っていない」状態が解消する。**この feature の
中心はそこ**である。

US2 は「その数字がいつのものか」を足すもので、独立して価値があるが US1 ほど重くない。

**新しい測定は 1 つも行わない。** 表示する事実はすべて既に測ってある(047 / 086 / 064 / 084)。
逆に、**測っていない量は 1 つも表示しない** — オッズの変動幅がその代表で、
目安として出したくなるが分布を一度も測っていない。
