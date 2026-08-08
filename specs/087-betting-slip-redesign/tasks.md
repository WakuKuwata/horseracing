# Tasks: 買い目推奨の金額主役カード表示 (Betting Slip Redesign)

**Input**: Design documents from `/specs/087-betting-slip-redesign/`

**Prerequisites**: plan.md(codex 記録欄=採否確定済み), spec.md, research.md, data-model.md, contracts/display.md, quickstart.md

**Tests**: 含む(FR-040 非交渉+codex H1/H2 の「テストは通るが検証しない」型対策を明示)。

**Organization**: 2026-08-08 codex レビュー(C1+H1-H8+M1-M4+L1 全採用)を反映した改訂版。**安全順序**: 答え合わせ表示(`RecommendationResults`)を先に抽出してからテーブルを置き換える=FR-022 を一瞬も退行させない。

## Format: `[ID] [P?] [Story] Description`

## Path Conventions

変更は全て `front/` パッケージ内。API/OpenAPI/DB/他パッケージは diff ゼロ(SC-005)。

---

## Phase 1: Setup & Gates

- [X] T001 実装前ベースライン確認 — **2026-08-08 実施: 26 files / 116 tests green・tsc 0・eslint 0**
- [X] T002 **codex ゲート消化** — 2 レンズ×各 2 セッションの結果を plan.md「Codex second opinion(記録欄)」に採否付きで記録済み(D2-D7 全 CHANGE 採用・C1+H1-H8+M1-M4+L1 全採用・不採用 1=中立開示文言改訂はスコープ外)。**Phase 2 以降のゲート解除**

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: UI から独立した純関数・hook・スタイル。テスト直当て

- [X] T003 `front/src/lib/budget.ts` を新規作成(純関数群): `validateBudget(input): number | null`(正の整数・100 未満拒否・100 の倍数強制なし・小数/NaN/Infinity/非 safe integer 拒否)、`computeAmount(stakeFraction, budget): AmountVM`(`raw=f×b` を **±ε 境界スナップ**(|raw−round(raw/100)×100| < max(1,|raw|)×Number.EPSILON×64 なら吸着)してから floor=codex D2。3 状態 reference/too_small/amount)、`summarizeAmounts(vms, budget)`(合計・点数・`Math.round(total/budget*100)`%・over-budget 判定)、`formatYen(n)`(ja-JP 単一経路=金額表記の統一)
- [X] T004 `front/src/lib/budget.test.ts`(純関数): (a) **実演算オペランド** `0.036×25000→¥900`・`0.35×22000→¥7,700`(素朴 floor だと 800/7600 に落ちる=回帰の核)、(b) ちょうど 100/99/null/0 境界、(c) validateBudget: 100/550 受理・空文字/99/0/負値/小数/NaN/Infinity 拒否(**validation と算術の分離**=codex M1)、(d) summarizeAmounts: 行数一致・整数% 境界・全行 too_small で ¥0/0 点・Σ>予算判定
- [X] T004A `front/src/lib/budget.ts` に `useBudget()` hook を追加+`front/src/lib/useBudget.test.tsx`: localStorage キー `horseracing.race_budget.v1`・**module-level memory fallback**(例外時も unmount→remount で保持=codex D6/H3)・render/initializer 内で書き込まない(StrictMode 二重評価安全)・復元値は validateBudget を通す。テスト: 100/550 保存・壊れた保存値拒否・remount 復元・getItem/setItem/removeItem 各例外(`vi.spyOn(Storage.prototype, …)`)で session fallback 動作・`<StrictMode>` wrapper 下で同結果・各テストで `localStorage.clear()`+`vi.restoreAllMocks()`。**複数タブ live sync は非要件と明記**
- [X] T005 [P] `front/src/lib/frameColors.ts` + `frameColors.test.ts`: `frameChipClass(frame)` 1..8 → `frame-chip--1..8`・null/0/9/undefined → 中立クラス
- [X] T006 [P] `front/src/lib/strength.ts` + `strength.test.ts`: `strengthOf(f, fMax)` — ε 許容の境界比較(`ratio > 2/3 + 1e-9 → 厚め`・`> 1/3 + 1e-9 → 標準`・以外 抑え=codex D4)・`f` null/負値/非有限 → null・`fMax <= 0` → null・バー幅 clamp [0,100]。テスト: **実演算境界 `0.1/0.3 → 抑え`・`0.2/0.3 → 標準`**・f=0→抑え・fMax=0→null・全行同値→全行「厚め」(明文化された仕様)
- [X] T007 `front/src/styles.css` に枠色チップ(1白[境界線+黒文字]/2黒[白文字]/3赤/4青/5黄[黒文字]/6緑/7橙/8桃[黒文字]・中立)・カード・強弱バー(装飾=aria-hidden 前提)・サマリのスタイル追加(損益を示唆する色の意味付け禁止)

**Checkpoint**: lib 単体テスト green

---

## Phase 3: US1+US3 一体 — 金額カード+答え合わせ view(FR-022 無退行ゲート)🎯 MVP

**Goal**: 予算×比率→円額のカード UI でテーブルを置き換える。**答え合わせは先に `RecommendationResults` へ抽出してから**差し替える(codex H4/M3 の安全順序)

**Phase 3 完了ゲート(codex H4)**: results view と現行 results 回帰テストが green になるまで Phase 3/MVP 完了・出荷可能とは扱わない。途中 commit は可だが checkpoint 扱いしない

**Independent Test**: quickstart §2 手順 1・2・5・6・7

- [X] T017B [US3] `front/src/components/RecommendationResults.tsx` を新規作成: 現行 `HitCell`・反実仮想回収列(行単位)・`WinBacktestSummary`・ベースライン表・オッズ帯別表を**計算式/値/表示意味不変**で移動(codex M3)。この時点では `RecommendationPanel` から従来どおり無条件描画=表示は現状維持
- [X] T008 [US1] `front/src/components/BudgetInput.tsx` を新規作成: **controlled component**(`budget`, `onBudgetChange` props・`useBudget` を自分で呼ばない=codex H3)。validateBudget を通った値のみ通知(550 円は丸めず受理)。`step=100` は入力ヒント。未設定時は入力促し文言(FR-005)
- [X] T009 [US1] `front/src/components/BetSlipCard.tsx` を新規作成: 券種・馬番(この段階は中立チップ)・`AmountVM` 表示(amount=最大サイズ・too_small=「少額のため見送り」・reference=「—(参考表示・賭け比率の記録なし)」)。**`double_pseudo=true` の行は主表示金額(見送り文言含む)を `<PseudoValue kind="double_pseudo">` で包む・実オッズ行は素のまま**(codex D3)。強弱ラベル+バー(props で受けた fMax 基準)。「根拠を見る」`<details>`(使用オッズ+実/推定バッジ・疑似オッズ・疑似 ROI・Kelly 比率=現行と同一の `PseudoValue` kind)
- [X] T010 [US1] `front/src/components/BetSlip.tsx` を新規作成: `summarizeAmounts` による購入サマリ(予算設定時のみ・全滅時 ¥0/0 点明示・Σ>予算で中立警告)+**グループ化前の全 items からレース全体で単一の fMax を計算し全カードへ**(codex H7・券種別再計算禁止)+カード列(API 応答順維持)+予算未設定フォールバック(カード上で Kelly 比率 `PseudoValue` 付き・強弱表示あり・円額なし)
- [X] T011 [US1] `front/src/components/RecommendationPanel.tsx` を書き換え: **`useBudget()` を 1 回だけ所有**し BudgetInput(controlled)/BetSlip に配線(codex H3)。8 列テーブルと券種 `<select>` を廃止し、view="slip" は BudgetInput+BetSlip、view="results" は T017B の `RecommendationResults` を描画。中立開示 `no-edge-note` は両 view で常時。`WIN_POLICY_MESSAGE` の既存挙動は維持(短絡除去は T019)
- [X] T017 [US3] view 切替を実装: `viewOverride: "slip"|"results"|null` + effective `viewOverride ?? (hasSettled ? "results" : "slip")`・**raceId 変更で override reset**(codex D7/H8)。トグルは hasSettled 時のみ(「買い目を見る」⇔「答え合わせを見る」)。確定後の slip 表示には**「現在の予算による換算・購入履歴ではありません」を常時表示**(codex 採用)。推奨が採用モデルの run に基づきモデルセレクタの影響を受けない旨の中立注記を追加
- [X] T012 [US1] `front/src/components/BetSlip.test.tsx` 新規+`RecommendationPanel.test.tsx` 更新: (a) 円額が全て 100 の倍数・サマリ合計一致(SC-007)、(b) 予算未設定で円額なし+促し文言、(b') フォールバック表示でも pseudo coverage 実行、(c) fraction null=参考表示・非算入、(d) 少額見送り・非算入、(f) 中立開示が slip/results 両方に常時+`/儲か|勝てる|稼げる|妙味|edge/i` 不一致、(g) **実オッズ行**の円額ノードが `data-pseudo` を持たない・**double_pseudo 行の金額は `data-pseudo-kind="double_pseudo"` で展開前から `toBeVisible()`**(codex D3)、(h) 予算変更→即時再計算、(i) Σ>予算の中立警告、(j) 全行 too_small の ¥0/0 点、(k) 点数と kind=amount 行数一致・整数% 境界・未設定時サマリ不存在(codex M1)
- [X] T012E [US1] pseudo 不変テスト(専用 MSW fixture・全行 settled=false): 各「根拠を見る」summary を `userEvent.click` — **クリック前 `open` なし・内部 pseudo node `not.toBeVisible()`・クリック後 `open`+visible を確認してから** `assertPseudoLabelCoverage` を実行(codex H1=closed `<details>` は DOM に残るため展開なしでも素通りする事故の対策)。fixture 内の**全 pseudo 値を期待値に列挙**し、`data-pseudo-kind` を estimated/pseudo/double_pseudo の期待どおり行単位で検証
- [X] T018 [US3] results 回帰テスト(codex H2 の意味等価リスト): (a) settled 行あり+**遅延 MSW 応答**で loading 解消後の初期 view が results(codex H8)・results→slip→results 双方向切替、(b) settled 行なしで slip 初期・トグル非描画、(c) hit/miss/roi の各 `[data-result]` が `[data-pseudo]` 配下にない・ResultBadge 維持、(d) 確定件数・的中・的中率・平均回収倍率が従来同値+「反実仮想」「参考」「将来の的中・利益を示すものではありません」文言維持、(e) no-bet ×1.00・favorite baseline・オッズ帯別を従来同値・baseline 内に `[data-result]` 非導入、(f) dead_heat ⚑/void「無効」回帰、(g) raceId 変更で settled→unsettled/逆を跨ぎ各レースの default view に戻る(override 持ち越しなし)

**Checkpoint**: quickstart §2-1/2/5/6/7 が通り、front テスト green(results 含む)。ここまでが出荷可能な MVP

---

## Phase 4: US2 — 枠色チップ・馬名・券種グループ

**Goal**: カードの見た目完成

**Independent Test**: quickstart §2 手順 3

- [X] T013 [US2] `front/src/pages/RaceDetailPage.tsx` を更新: `RecommendationPanel` に **`entries={raceQuery.data?.horses}`** を渡す(**`RaceDetail.horses: HorseEntry[]` が正**。`predQuery.data?.horses` は `HorsePrediction[]` で horse_name/frame を持たないため使用しない=codex C1)。パネル内で追加 query を呼ばない
- [X] T013A [US2] `front/src/pages/RaceDetailPage.test.tsx` に MSW 統合テスト: race detail 応答のみに horse_name/frame を設定(prediction 応答には設定しない)し、買い目カードに馬名と枠色クラスが出ることを確認
- [X] T014 [US2] `BetSlipCard.tsx` 更新: `selection` の**全馬番**を entries と照合。各馬で horse_name と frame を**独立に縮退**(codex H6): (1) name null+frame あり=枠色維持・馬名なし、(2) frame null+name あり=中立チップ・馬名維持、(3) 照合不能/prop なし=中立チップ・馬番のみ。クラッシュ・行非表示禁止
- [X] T015 [US2] `BetSlip.tsx` 更新: `bet_type` ごとのグループ見出し(`betTypeLabel`・グループ順=API 初出順・グループ内=応答順)。fMax はレース全体計算のまま(券種別に再計算しない)
- [X] T016 [US2] テスト追加: (a) frame 1..8 → 対応クラス・null → 中立、(b) 馬名併記・照合不能で馬番のみ、(c) entries 未提供でもクラッシュしない、(d) 券種グループ見出し、(e) 強弱がレース全体 fMax 基準・null 行に出ない
- [X] T016B [US2] 複数頭 selection(例: 三連複)で一頭=name 欠損/frame あり・別頭=name あり/frame 欠損とし、**欠損項目だけが独立に縮退**することを確認
- [X] T016C [US2] 別券種に最大値と 1/3・2/3 境界値を配置した fixture で、強弱が**券種内最大でなくレース全体最大**を基準にすることを確認(codex H7)
- [X] T016D [US2] 同一 bet_type を金額・疑似 ROI・馬番の非昇順で返す fixture で、カードが **API 応答順のまま**であることを DOM 順序で確認(ソート禁止の機械検証=codex M2)

**Checkpoint**: quickstart §2-3 が通る

---

## Phase 5: US4 — 見送りの能動表示

**Independent Test**: quickstart §2 手順 8

- [X] T019 [US4] `BetSlip.tsx` に見送り/空状態カードを追加すると同時に、`RecommendationPanel.tsx` の **`QueryStateView` empty 短絡を除去**(codex H5): 成功応答の `items=[]` も BetSlip へ渡し、no_run/not_generated/no_win_selected は対応カード、**generated/未知 status は既存「この条件の推奨はありません」を fallback**。loading/error の扱いは維持。win 行なし+exotic 行ありのレースでは「単勝は見送り」カードと exotic カードを同時表示
- [X] T020 [US4] テスト追加: (a) items=[] × 3 status の各文言、(b) **generated + items=[] の既存 fallback**(現行テストの維持)、(c) no_win_selected+exotic 残存で見送りカードと exotic カード同時表示

**Checkpoint**: 全 user story 完了

---

## Phase 6: Polish & Cross-Cutting

- [X] T021 [P] 全体回帰: `cd front && pnpm test && pnpm exec tsc -b && pnpm exec eslint . && pnpm build && pnpm check:openapi` 全 green(codex L1: drift-check を明示実行)
- [X] T022 [P] 変更範囲検証: `git diff --stat` が front の表示層ファイルのみ・`front/openapi.json` と `front/src/api/**` に diff なし(contracts §7)
- [X] T023 quickstart §2 の画面検証を実 DB で実施(確定済み・見送り・旧行 null は 2024-12-28 各レース、**未確定ケースは結果未取込の別日**)。§3 回帰(同着・void・localStorage 無効)も実施し結果を quickstart 末尾へ記録
- [X] T024 禁止事項・a11y 最終走査: 利益示唆語/損益色/EV ソート不在(FR-041)・中立開示両 view 常時(FR-042)・「少額のため見送り」と policy 見送りの文言が混同されないか確認・view 切替はボタン意味論・強弱バーは `aria-hidden`(ラベル文字が本体)
- [X] T025 メモリ・引き継ぎ更新: `betting-slip-redesign-decisions` メモリと CLAUDE.md の 087 エントリを実装結果(テスト数・E2E・codex 採否)で更新

---

## Dependencies

- T002(codex ゲート)✅ 消化済み → Phase 2 解除
- Phase 2(T003-T007)→ Phase 3 の前提。T003 → T004 → T004A は直列(codex M4: [P] 撤回)
- **Phase 3 は T017B(results 抽出)を最初に**実施 — テーブル削除(T011)前に答え合わせの受け皿を用意し FR-022 を無退行に保つ
- Phase 4(US2)・Phase 5(US4)は Phase 3 完了後、相互に独立(並行可)
- Phase 6 は全 story 完了後

## Implementation Strategy

**MVP = Phase 1-3**(US1+US3 一体)。codex H4 の指摘どおり「買い目カードだけで出荷可能」は撤回し、答え合わせ view の green までを MVP 完了条件とする。以後 US2(見た目完成)→ US4(見送りカード)→ Polish の順。各 checkpoint で front テスト green を維持する。
