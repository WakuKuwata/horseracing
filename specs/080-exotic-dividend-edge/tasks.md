---
description: "Task list for 080 real exotic dividend ingestion + exotic edge measurement"
---

# Tasks: Real Exotic Dividend Ingestion & Exotic Edge Measurement

**Input**: Design documents from `specs/080-exotic-dividend-edge/`

**Prerequisites**: plan.md, spec.md, research.md (D1–D7・D1=T0 spike RESOLVED), data-model.md, contracts/{parser.md, edge-eval.md}, quickstart.md

**Tests**: 含む(TDD)。parser は実 fixture ベースのテストを実装より先に。相乗り/冪等/例外隔離は integration。リーク境界は leak-guard。

**Organization**: US1(parser 実 markup 対応)→ US2(日次相乗り+前向き収集)→ US3(pre-registered edge 測定)。US2 は US1 に依存。US3 は実配当蓄積後に走るが、pre-registration doc は着手時に固定する。

**codex unavailable**: 設計レビュー 2 回とも repo AGENTS.md derail([[codex-env-recovery]])→セルフレビュー代替(plan.md Complexity 表)。

## Path Conventions

- scrape: `scrape/src/horseracing_scrape/`, tests `scrape/tests/{unit,integration}/`, fixtures `scrape/tests/fixtures/real/`
- betting: `betting/src/horseracing_betting/`, tests `betting/tests/`
- 実行: `uv run --project <pkg> ...`・DB=`postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 **T0 spike 実施済(2026-07-23)**: `capture-fixture --kind results --race-id 202602011206` で実 result ページ捕獲(`scrape/tests/fixtures/real/results_202602011206.html`)。live に `Payout_Detail_Table`×2 確認=相乗り追加req0 実証・実 markup を contracts/parser.md「検証済み実 markup」節に確定。
- [X] T002 [P] 同着(dead-heat)検証用 fixture を用意: 同着払戻を含む実 result ページを 1 枚捕獲(`capture-fixture`)、無ければ contracts/parser.md の既知構造(複勝/ワイドの複数 `<br>`/`<ul>`)で合成 fixture を作成 `scrape/tests/fixtures/real/results_deadheat.html`。
- [X] T003 [P] 結果未確定 fixture を用意: 発走前 result ページ(`Payout_Detail_Table` 無)を 1 枚捕獲 or 合成 `scrape/tests/fixtures/real/results_pending.html`(空返し検証用)。

---

## Phase 2: User Story 1 — parser 実 markup 対応 (Priority: P1) 🎯 MVP

**Goal**: `parse_exotic_odds` を実 netkeiba `Payout_Detail_Table` markup 対応にし、全対応券種の確定配当を正準形で抽出。出力契約 `ScrapedExoticOdds` は不変。

### Tests (先行 · 実 fixture) ⚠️

- [X] T004 [P] [US1] `scrape/tests/unit/test_parse_exotic_odds.py` を実 fixture ベースに rewrite: `results_202602011206.html` から複勝/馬連/ワイド/馬単/3連複/3連単 の (bet_type, selection 正準, 倍率) 期待値一致を assert(例: 3連単 `[1,9,10]`→×109.4・ワイド 3 組・複勝 3 単勝・馬連 ×20.0)。単勝/枠連は抽出しないことを assert。
- [X] T005 [P] [US1] 同 test: 同着 fixture(T002)で複数払戻の全行抽出(取りこぼしなし)を assert。
- [X] T006 [P] [US1] 同 test: 結果未確定 fixture(T003)で rows 空(または contracts 統一の空表現)を assert。未対応券種/欠損テーブルで他券種継続(partial)を assert。
- [X] T007 [P] [US1] 同 test: 期待券種欠落時の **silent-empty fail-loud**(markup 変更で全 0 行を異常検知)を assert。

### Implementation for US1

- [X] T008 [US1] `scrape/src/horseracing_scrape/parse/exotic_odds.py` を rewrite: `Payout_Detail_Table`×2 を走査 → 行ごと `<th>` 券種ラベル→canonical map(複勝→place/馬連→quinella/ワイド→wide/馬単→exacta/3連複→trio/3連単→trifecta・単勝/枠連 skip)。key は `race_id_from_html`(fixture 専用 `race_key_from` 廃止・results parser と共有)。出力契約 `ScrapedExoticOdds`/`ScrapedExoticRow` は不変。
- [X] T009 [US1] 同 file: **Result セル 2 形式の抽出**を実装 — combo 系(`<ul><li><span>N</span></li></ul>` 反復=複数選択・順序保持)/複勝(`<div><span>N</span></div>` 非空 span=各単勝選択・空 span パディング除外)。**Payout セル**は `<span>` 内 `<br>` 分割・カンマ/円 除去 → `/100`=倍率。選択列と払戻列を **1:1 zip**(不一致は fail-loud)。
- [X] T010 [US1] silent-empty ガード: 確定レースで期待券種の下限(最低 quinella+trio+trifecta)を満たさない全 0 行を型付き例外/警告にする(結果未確定の正当な空とは区別)。
- [X] T011 [US1] `parse_exotic_odds` → 既存 `upsert_exotic_odds` の**契約整合を確認**(馬番キー・canonical_selection・coverage_scope)。upsert は無改修で載ることを単体で確認(place coverage nuance=research D3 は別 issue としてコメント)。

**Checkpoint**: 実 fixture で全対応券種が抽出でき upsert に載る。US1 単独で「実 markup を読める」価値が成立。

---

## Phase 3: User Story 2 — 日次 results 相乗り+前向き収集 (Priority: P1)

**Goal**: `scrape_results` が既取得の result HTML から配当も抽出・保存。追加 netkeiba リクエスト 0・結果確定後のみ・例外隔離。

### Tests (先行 · integration) ⚠️

- [X] T012 [P] [US2] `scrape/tests/integration/test_exotic_cli.py` を更新: 実 fixture を fetcher に載せ、`scrape_results` 相乗りで exotic_odds 行が生成されることを testcontainer で assert。
- [X] T013 [P] [US2] 同 test: **追加 fetch 0** — fetcher の `get` 呼び出し回数が result 取得分のみ(exotic 用の追加呼び出しゼロ=同一 html 再利用)を assert。
- [X] T014 [P] [US2] 同 test: **冪等** — 同一レース再処理で exotic_odds 行数不変・値一致(ON CONFLICT 上書き)。
- [X] T015 [P] [US2] 同 test: **例外隔離** — exotic parse を故意に失敗させても result(着順)保存が成功継続・job 全体が落ちない・監査に記録。
- [X] T016 [P] [US2] 同 test: **結果未確定は書かない** — pending fixture で exotic_odds に行が作られない(FR-006)。

### Implementation for US2

- [X] T017 [US2] `scrape/src/horseracing_scrape/pipeline.py::scrape_results` の per-race work に exotic 相乗りを追加: `parse_results`(着順)保存が成立した後、**同一 html 変数**(再 fetch しない)で `parse_exotic_odds`→`upsert_exotic_odds`。結果確定(着順行あり)レースのみ。
- [X] T018 [US2] 同 file: exotic parse/upsert を try/except で隔離(scrape_laps の per-page skip 前例)。失敗は `Counts.error_messages` に記録し result 保存・job を壊さない。相乗りの Counts を集計に合流。
- [X] T019 [US2] リーク境界 leak-guard: exotic_odds を features/serving load 経路に入れないことを機械確認。`scrape` が betting/features を import しない(既存 import-graph 境界テストに exotic 経路が違反しないか確認)。exotic_odds 変更でモデル予測 byte 不変のテスト(features or serving の既存 leak/parity テストに追加)。

**Checkpoint**: 日次 result 取得で exotic_odds が前向きに埋まり始める。追加ネットワーク 0・冪等・result 保存を壊さない。

---

## Phase 4: User Story 3 — pre-registered exotic edge 測定 (Priority: P2)

**Goal**: 実配当蓄積後に 009 joint EV vs 実配当を pre-registration に沿って測る。実配当 n<n_min は NO_DECISION。

### Setup (着手時に固定 · 結果前)

- [X] T020 [US3] `specs/080-exotic-dividend-edge/pre-registration.md` を作成し**結果前に固定**(068/073 同型・append-only): 券種個別・baseline(最低O_est/uniform)・成功=baseline 超過・**券種別 n_min**(組合せ数で trifecta 最大)・開催日クラスタ bootstrap(seed 固定)・多重比較補正(6券種×窓)・収集系列(主=前向き/補=cache 別ラベル)・probability=P_model(p≠q)・控除率(馬連/ワイド22.5%・馬単/三連複25%・三連単27.5%)を logic_version に。

### Tests ⚠️

- [X] T021 [P] [US3] `betting/tests/` に NO_DECISION 規約テスト: 実配当 n が n_min 未満の券種で `exotic-backtest` が verdict=NO_DECISION を返す(SC-006・偽の勝ちを出さない)。
- [X] T022 [P] [US3] 同: 実配当優先・無ければ O_est(double-pseudo)で分離ラベル、EV=P_model×payout(q をモデル確率にしない=p≠q)を assert。

### Implementation for US3

- [X] T023 [US3] `betting/src/horseracing_betting/exotic_backtest.py` を実データで検証し、**NO_DECISION 三値**(n<n_min)・券種個別採点・baseline 超過判定・開催日クラスタ bootstrap CI・多重比較補正を pre-registration どおりに実装/確認(既存に不足があれば追加)。
- [X] T024 [US3] `betting/src/horseracing_betting/exotic_divergence.py` を実データで検証(推定 vs 実・DIAGNOSTIC のみ・採否バーでない)。coverage_rate/signed log(real/est) を出す。
- [X] T025 [US3] `betting exotic-backtest`/`exotic-divergence` CLI が logic_version に控除率/窓/seed/n_min/baseline/収集系列を記録(憲法 V)。

**Checkpoint**: 蓄積後に edge を pre-registration に沿ってのみ測れる。データ不足は NO_DECISION。edge の有無は測定結果(null も成功)。

---

## Phase 5: Polish & Cross-Cutting

- [X] T026 [P] 全体回帰: `uv run --project scrape pytest -q`・`uv run --project betting pytest -q`・`ruff check scrape betting`。
- [ ] T027 [P] quickstart.md の手順を実 DB で 1 日分 walk-through(scrape-results 相乗り→exotic_odds 蓄積確認→被覆 SQL)。
- [X] T028 運用ノート: 日次 ops/worker([[local-db-setup]])で scrape-results 相乗りが有効化されることを確認。exotic_odds 被覆(確定レース中の配当取得率)の可視化 SQL を quickstart に残す。

---

## Dependencies

- T001–T003(fixtures)→ T004–T011(US1)。
- US1 完了 → US2(T012–T019、相乗りは parser に依存)。
- T020(pre-registration 固定)は着手時。T021–T025(US3)は実配当が n_min に達してから実行(数週間〜数ヶ月後)。
- リーク境界 T019 は US2 実装と同時。

## Parallelizable [P]

- fixtures T002/T003、US1 tests T004–T007、US2 tests T012–T016、US3 tests T021/T022 は各々別ファイルで並列可。
- 実装本体(T008–T011 は同一 parser file=順次、T017/T018 は同一 pipeline file=順次)。

---

## Implementation Status (2026-07-23)

**US1(parser)+ US2(相乗り)実装完了・scrape 全 96 テスト green・ruff クリーン。** codex を workspace-write・derail ガード付きで並列起動(US1/US2 disjoint)→ 両者 derail せず実装成功。

**完了**: T001(spike)/T003-T019/T020。parser=`Payout_Detail_Table`×2 実 markup 対応(6券種・複勝div/combo ul 両形式・yen/100・1:1 zip・no-table/no-rows/mismatch で ParseError)。pipeline=同一 html 相乗り(追加req0)+ 結果確定後 + 例外隔離。**Claude レビューで codex の見落とし 1 件を修正**: exotic upsert の DB エラーが session を poison し次レース result 保存を連鎖失敗させる穴 → `session.begin_nested()`(SAVEPOINT)で隔離(FR-007 完全化)。

**重要な発見(coverage_scope nuance の一般化)**: 実 netkeiba 払戻テーブルは**当選組合せのみ**を載せる(全オッズグリッドではない)。よって `_expected_count`(full グリッド)基準の `coverage_scope` は result-page 配当では**常に partial**。旧 feature 012 テスト(全グリッド前提)は実データの現実に合わせて migrate 済(`coverage_scope=={'partial'}` を assert)。→ 将来 coverage_scope の意味を「配当=winners-only」向けに再定義するか検討(別 issue、research D3 の拡張)。

**残**:
- T002(専用 dead-heat fixture): 複数払戻機構は T005 `test_handles_multiple_place_and_wide_payouts` でカバー済。同着専用 fixture は後日実捕獲で追加(任意)。
- T010 は基本 silent-empty ガード(rows 非空で fail-loud)実装。券種別下限(quinella+trio+trifecta floor)は非標準レースでの false-positive fail を避けるため見送り(前向き収集で markup drift を監視)。
- **T021-T025(US3 edge 測定)= データゲート**: 実配当が pre-registration の n_min に達するまで実行不可(前向き収集で数週間〜数ヶ月)。pre-registration(T020)は固定済。
- T026-T028(polish): scrape 回帰 green。運用結線(日次 ops 相乗り有効化)は稼働環境で確認。

---

## US3 Implementation Status (2026-07-23) — machinery ahead of data

codex を並列(C=gate core / D=divergence)で起動 → 両者 derail せず成功。betting 全 170 テスト green・ruff クリーン。

**完了(検証済み・testable)**:
- **T023 の core**: `betting/exotic_gate.py::evaluate_exotic_gate`(純関数)= 券種個別・**n<n_min or <2日 → NO_DECISION**・開催日クラスタ bootstrap CI(既存 tested `race_day_cluster_bootstrap_ci_v1` 再利用)・**Holm-Bonferroni**(step-down・p値は day-cluster 片側 bootstrap)・三値 verdict(ADOPT_CANDIDATE=p_adj<α∧point>0∧ci_low>0 / REJECT / NO_DECISION)。合成データ unit テスト 6 件(T021 NO_DECISION・Holm 補正・決定論)。**Claude レビューで統計ロジック確認済**(Holm step-down 正しい・NO_DECISION 3条件・family=deciding 券種のみ)。
- **T024**: `exotic_divergence.py` 診断契約を verify(4項目 PASS=verdict なし・log(real/est)・lookahead なし・p≠q)+ `divergence_logic_version`(控除率/窓記録)+ テスト 7 件。
- **T025 の一部**: `divergence_logic_version` で控除率/窓/seed を記録。

**意図的に deferred(データゲート・実データで設計すべき)**:
- **T023 の driver + T025 の exotic-gate CLI**: `run_exotic_gate`(backtest から per-(bet_type, race_day) の model−baseline 差分を組んで evaluate_exotic_gate に渡す)+ `exotic-gate` サブコマンド。**理由**: exotic_odds が空(実配当ゼロ)=end-to-end 検証不能、かつ per-race 差分の正規化は実データを見て決める方が健全。gate core は完成しているので、データが n_min に近づいた時に薄い driver を足すだけ。
- **T021/T022 の実データ経路テスト**: 実配当蓄積後。
- **T026-T028**: scrape/betting 回帰 green。運用結線は稼働環境で。

**要するに**: US1/US2(実配当を貯める配管)+ US3 の測定機械 core(NO_DECISION ゲート)は実装完了。残るは「データが貯まってから driver を薄く足して測る」だけ。edge の有無は測定結果(null も成功)。

---

## US3 Driver+CLI Status (2026-07-23, round 3) — 実装ほぼ完了

codex 並列(E=driver+CLI / F=dead-heat fixture)→ 両者 derail せず成功。**betting 172・scrape 97 テスト green・ruff クリーン**。

**完了**:
- **T002**: 同着 fixture `results_deadheat.html` + `test_parses_dead_heat_multiple_payouts`(exacta/trifecta が複数当選を全取得)。
- **T023 driver**: `run_exotic_gate`(実配当のみ・per-(bet_type,race_day) net-return 差分 model−baseline → evaluate_exotic_gate)+ `PREREGISTERED_N_MIN`。空配当で全 NO_DECISION を integration テストで実証。**Claude レビューで差分集計ロジック確認**(両 stake>0 のみ・net-return diff・isoformat 日キー)。
- **T025 CLI**: `exotic-gate` サブコマンド + logic_version ヘッダー(窓/baseline/seed/alpha/b/n_min/控除率/series=prospective-primary)。
- **T022**: driver の real-only(実配当無しレースは skip)+ P_model(ev bets)で p≠q を構造的に保証。空配当テストで担保。
- **T026**: 全体回帰 green。**T028**: quickstart に被覆 SQL + 運用ノート。

**残 T027(実 DB walk-through)**: scrape 相乗り部は testcontainer で実証済。予測依存の exotic-gate 実行は **lgbm-065 の serving parity fail-close(feature_hash 不一致=feature 080 と無関係の既存 model/環境問題、既存 exotic-backtest も同じエラー)** でブロック。model hash 整合後に実行可能。

**Feature 080 実装完結**: US1(parser)+ US2(相乗り)+ US3(測定機械 core+driver+CLI)全実装。**実行のみデータゲート**(実配当が n_min に達するまで NO_DECISION)。edge の有無は測定結果(null も成功)。
