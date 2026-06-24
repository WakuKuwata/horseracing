# Tasks: exotic EV 推奨と疑似ROIバックテスト

**Input**: Design documents from `specs/011-exotic-ev-recommendation/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/exotic_recommend.md, contracts/exotic_backtest.md, quickstart.md

**Tests**: 含む(憲法 III 評価先行 + spec の Independent Test/Acceptance Scenarios。確率/EV/採点/リーク境界はテスト必須)

**Organization**: User story(P1 US1 → P1 US2 → P2 US3)単位。MVP=US1。

## パス規約

既存 `betting/`(`horseracing-betting`)を拡張。src は `betting/src/horseracing_betting/`、テストは `betting/tests/{unit,integration}/`。
全パスはリポジトリルート相対。

---

## Phase 1: Setup(共有基盤)

- [x] T001 `betting/pyproject.toml` の `dependencies` に `horseracing-probability` を追加し、ルート `pyproject.toml`/uv workspace の path 解決(009/010 を提供)を確認する
- [x] T002 `cd betting && uv sync` を実行し、`from horseracing_probability.engine import joint_probabilities` と `from horseracing_probability.market_odds import estimate_market_odds` が import できることを確認する
- [x] T003 [P] `betting/src/horseracing_betting/exotic_types.py` に exotic 共通の値オブジェクト(`BetType` 別 `ALL_EXOTIC` 定数、`Selection` TypedDict/dataclass、`CanonicalField`、`ExoticBet`、`ScoredBet`、`ExoticRoiReport`)の骨子を定義する(data-model.md §1–6 準拠)

**Checkpoint**: betting が probability に依存し import 成功、共通型が存在。

---

## Phase 2: Foundational(全 US の前提・ブロッキング)

**⚠️ US1/US2/US3 すべてが依存する。先に完了させること。**

- [x] T004 [P] `betting/src/horseracing_betting/exotic_selection.py` に selection シリアライズを実装する: `to_selection(bet_type, key)`(009/010 の tuple/frozenset → JSONB 安全配列。順序券種=順序保持、無順序=horse_number 昇順整列、単一=`[i]`)、`selection_key(selection)`(決定論タイブレーク文字列)(research.md R2 / data-model.md §3 / contracts/exotic_recommend.md)
- [x] T005 `betting/src/horseracing_betting/exotic_selection.py` に券種別的中判定 `is_hit(selection, finish_order, *, field_size)` を実装する: exacta/trifecta=順序一致、quinella/trio=集合一致、wide/place=包含 + field 規則(8頭+:top3、5–7頭:top2、≤4:対象外、009 と共有)(research.md R3 / data-model.md §4)
- [x] T006 [P] `betting/src/horseracing_betting/exotic_ev.py` に `canonical_field(predictions, odds, *, scratched=())` を実装する: 母集団=`win_prob>0` かつ `odds>0` かつ 非出走ステータス除外(出走取消/競走除外/取消、FR-011)、`p_norm` を Σ=1 再正規化、`odds_norm` を母集団に絞り、欠損馬は `excluded`(reason: no_prob/no_odds/scratched/競走除外/出走取消)へ。**空母集団(≤1頭)は空 dict を返し正規化しない**。不変 `set(p_norm)==set(odds_norm)==set(horse_numbers)`(research.md R1 / data-model.md §1 / contracts/exotic_recommend.md)

### Foundational テスト

- [x] T007 [P] `betting/tests/unit/test_exotic_selection.py` を作成: selection 往復(順序保持/昇順整列/frozenset 非保存)・selection_key 安定性・券種別 is_hit(順序/集合/包含 + field 規則 top2/top3/none・同着)を検証(SC-003/SC-004)
- [x] T008 [P] `betting/tests/unit/test_canonical_field.py` を作成: p のみ/odds のみ/各非出走ステータス(出走取消/競走除外)の馬の除外、残存再正規化(Σ=1)、`p_norm` と `odds_norm` の母集団一致、空母集団(≤1頭)が空 dict・正規化なし(0 除算しない)を検証(SC-002)

**Checkpoint**: selection/的中/canonical 母集団が単体で動作・検証済み。US1/US2 を着手可能。

---

## Phase 3: User Story 1 - exotic EV 買い目を生成して保存できる (Priority: P1) 🎯 MVP

**Goal**: prediction_run/レース指定で各 exotic 券種の EV≥閾値 上位 K を `recommendations` に保存。

**Independent Test**: ある prediction_run と win オッズで exotic 推奨を生成し、各券種で EV≥閾値 上位 K のみが
`recommendations`(is_estimated_odds=true、estimated_market_odds_used=O_est、pseudo_odds=1/P_model、pseudo_roi=EV−1、
JSONB 安全 selection)で保存されることを確認。

### 実装

- [x] T009 [US1] `betting/src/horseracing_betting/exotic_ev.py` に `exotic_ev_bets(field, *, threshold=1.0, top_k=5, bet_types=ALL_EXOTIC, payout_rates=None, odds_cap=10000.0)` を実装する: `joint_probabilities(field.p_norm, field_size)` で P_model、`estimate_market_odds(field.odds_norm, field_size, payout_rates, odds_cap)` で O_est、共通キーで `ev=p_model*o_est`、O_est None/cap 超過・P_model→0 は候補除外、selection 正準化、`ev≥threshold` を `(-ev, selection_key)` で整列し券種別 top_k(research.md R1/R5 / contracts/exotic_recommend.md)
- [x] T010 [US1] `betting/src/horseracing_betting/exotic_recommend.py` に `default_exotic_logic_version()`(EV式/閾値/K/stake/控除率/q ソース/cap/母集団ポリシー/009/010 版を含む)を実装する(FR-006)
- [x] T011 [US1] `betting/src/horseracing_betting/exotic_recommend.py` に `generate_exotic_recommendations(session, *, race_id, prediction_run_id, threshold=1.0, top_k=5, stake=100.0, bet_types=ALL_EXOTIC, payout_rates=None, odds_cap=10000.0, logic_version=None)` を実装する: race_predictions(p)+ race_horses.odds(q)+ entry_status を読み `canonical_field`→`exotic_ev_bets`→`recommendations` を append-only INSERT。全列(`bet_type`/`selection`(JSONB 安全配列)/`market_odds_used=None`/`estimated_market_odds_used=o_est`/`is_estimated_odds=True`/`pseudo_odds=1/p_model`/`pseudo_roi=ev−1`/`logic_version`/`computed_at`/`prediction_run_id`/`race_id`)。結果非参照(FR-004/FR-005/contracts)

### US1 テスト

- [x] T012 [P] [US1] `betting/tests/unit/test_exotic_ev_select.py` を作成: 合成 p/odds で `EV=P_model×O_est`、EV≥閾値 上位 K 制限、券種別 K、決定論順序((-EV, selection_key))、P_model→0/O_est cap の候補除外、p≠q(p と q の取り違えがないこと)を検証(SC-001/SC-002/FR-001/FR-003)
- [x] T013 [P] [US1] `betting/tests/integration/test_exotic_recommend.py` を作成: 実 DB(testcontainers + _synth)で `generate_exotic_recommendations` が `recommendations` に券種別 EV 上位 K を保存し、market_odds_used=null/is_estimated_odds=true/estimated_market_odds_used=O_est/pseudo_odds=1/P_model/pseudo_roi=EV−1/JSONB 安全 selection/computed_at/logic_version が揃い、append-only・決定論であることを検証。**リーク境界テスト**: `race_results` を変更/注入しても生成される推奨が不変(生成が結果を読まない)ことを assert(SC-001/SC-003/SC-007/FR-004)

**Checkpoint**: US1 単独で動作・テスト緑。exotic 推奨が生成・保存される(MVP 完成)。

---

## Phase 4: User Story 2 - exotic の疑似ROIバックテストで baseline と比較 (Priority: P1)

**Goal**: 期間指定で EV 戦略の疑似ROI(払戻=stake×O_est=二重疑似)を券種別 baseline と同一条件比較。

**Independent Test**: 合成データで EV 戦略と baseline を同一レース集合で走らせ、券種別的中(順序/無順序/包含)・複勝/ワイドの
複数当たり(ベット単位)・回収率/的中率/見送り率/最大DD/最大連敗が算出され、全出力が二重疑似明示されることを確認。

### 実装

- [x] T014 [P] [US2] `betting/src/horseracing_betting/exotic_roi.py` に `score_exotic(bets, outcome, *, stake)` と `aggregate_roi(scored)` を実装する: `is_hit` で採点、`payout=stake*o_est` if hit else 0、複勝/ワイドはベット単位(R4)、券種別+総合で n_bets/n_hits/hit_rate/total_stake/total_payout/roi/skip_rate/max_drawdown/max_consecutive_losses、`pseudo=True` 固定(FR-007/FR-008/FR-010/contracts/exotic_backtest.md)
- [x] T015 [P] [US2] `betting/src/horseracing_betting/exotic_strategies.py` に `lowest_oest_baseline(field, *, top_k, bet_types, ...)`(各券種 O_est 最小=市場最有力、タイブレーク `(o_est, selection_key)` 昇順)と `uniform_baseline(field, *, top_k, bet_types, seed=DEFAULT_SEED, ...)`(決定論シードで K 点均等抽出、`DEFAULT_SEED` 定数を定義)を実装する。EV 戦略と同一 canonical_field/selection/採点経路(FR-009/research.md R6)
- [x] T016 [US2] `betting/src/horseracing_betting/exotic_backtest.py` に `run_exotic_backtest(session, *, date_from, date_to, threshold=1.0, top_k=5, stake=100.0, bet_types=ALL_EXOTIC, payout_rates=None, odds_cap=10000.0, seed=DEFAULT_SEED, prediction_run_id=None, model_version=None, strategies=("ev","lowest_oest","uniform"))` を実装する: **予測ソースの決定論規則**(prediction_run_id 指定→それ、無→model_version 既定=採用中モデルの当該レース予測、複数該当は最新 computed_at)で predictions を選び、各レースで canonical_field→各戦略 bets→`race_results`(finished, finish_order→finish_pos)で `ExoticRaceOutcome`(field_size=canonical)構築→採点→`opportunities`/`skipped` を集計に渡す。買い目生成は結果非参照。同着で順位非一意はレーススキップ+監査(place/wide は圏内同着的中)、DNF=外れ、推定不能=母集団除外、結果未確定レース除外(FR-004/FR-011/contracts)

### US2 テスト

- [x] T017 [P] [US2] `betting/tests/unit/test_exotic_roi.py` を作成: 券種別的中(馬単/三連単=順序、馬連/三連複=集合、ワイド=top3 内 2 頭、複勝=圏内)、**field_size 境界**(8頭+→top3、5–7頭→top2、≤4頭→対象外)を全分岐でベット単位 payout 検証、複勝/ワイドの複数当たりがベット単位で各々 stake×O_est 払戻(レースでキャップしない)、skip_rate は opportunities/skipped から算出、回収率/的中率/最大DD/最大連敗、pseudo=True を検証(SC-004/FR-008)
- [x] T018 [P] [US2] `betting/tests/unit/test_exotic_strategies.py` を作成: lowest_oest=各券種 O_est 最小選択 +`(o_est, selection_key)` タイブレーク、uniform=`DEFAULT_SEED` 決定論、EV/baseline が同一 canonical 母集団・stake・K・採点で比較され、成功=各 baseline 超え(>1.0 ではない)、決定論を検証(SC-005/FR-009)
- [x] T019 [P] [US2] `betting/tests/integration/test_exotic_backtest.py` を作成: 実 DB で期間バックテストが EV/lowest_oest/uniform の券種別 ExoticRoiReport を返し、予測ソース決定論規則(prediction_run_id/model_version)、結果未確定/同着/推定不能レースを規則どおり扱い、**買い目生成段階で結果非参照**(生成 bets が結果に不依存)、全出力 pseudo=True、決定論を検証(SC-004/SC-005/SC-006/SC-007/FR-004)

**Checkpoint**: US2 単独で動作・テスト緑。EV vs baseline の疑似ROI 比較が可能。

---

## Phase 5: User Story 3 - CLI で exotic 推奨生成とバックテスト (Priority: P2)

**Goal**: CLI でレース/予測実行指定の推奨生成、期間指定のバックテスト。閾値/K/stake/券種を設定可能。

**Independent Test**: CLI で exotic 推奨生成(レース指定)とバックテスト(期間指定)を実行し、券種別推奨件数/疑似ROI 指標/
baseline 比較/二重疑似明示が表示される。

### 実装

- [x] T020 [US3] `betting/src/horseracing_betting/cli.py` に `exotic-recommend` サブコマンド(`--race-id --run-id --threshold --top-k --stake --bet-types`)を追加する: `generate_exotic_recommendations` を呼び、券種別の保存件数と各買い目の EV を表示、全行に「**二重疑似(モデル確率 × 推定市場オッズ)/ is_estimated_odds=true / market_odds_used=null**」を明示(FR-010/FR-013/contracts/exotic_recommend.md)
- [x] T021 [US3] `betting/src/horseracing_betting/cli.py` に `exotic-backtest` サブコマンド(`--from --to --threshold --top-k --stake --bet-types`)を追加する: `run_exotic_backtest` を呼び、EV/lowest_oest/uniform の券種別 ROI/的中率/見送り率/DD/連敗を表示、冒頭に「二重疑似(推定オッズ + PL 外挿)評価」を明示(FR-013/FR-010)

### US3 テスト

- [x] T022 [P] [US3] `betting/tests/integration/test_exotic_cli.py` を作成: `exotic-recommend`/`exotic-backtest` が実 DB で実行され、推奨件数・EV・券種別疑似ROI・baseline 比較・**二重疑似ラベル(両コマンド出力に存在)** が含まれることを検証(FR-010/FR-013)

**Checkpoint**: 全 US 完了。CLI から推奨生成とバックテストが操作可能。

---

## Phase 6: Polish & Cross-Cutting

- [x] T023 [P] `betting/src/horseracing_betting/__init__.py` に exotic 公開 API(canonical_field/exotic_ev_bets/generate_exotic_recommendations/run_exotic_backtest)をエクスポートする
- [x] T024 [P] `cd betting && uv run ruff check . && uv run ruff format --check .` を実行し lint/format を解消する
- [x] T025 `cd betting && uv run pytest tests/unit && uv run pytest -m integration` を実行し全テスト緑を確認する
- [x] T026 [P] [quickstart 検証] `specs/011-exotic-ev-recommendation/quickstart.md` の SC-001〜SC-007 手順を実 DB(2008 データ + 活性 prediction_run)で実行し、推奨生成→保存→監査・バックテスト→baseline 比較・二重疑似明示を確認する

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001→T002 順、T003 は [P]。
- **Phase 2 (Foundational)**: Setup 後。T004→T005(同一 exotic_selection.py、順次)、T006 は [P]。テスト T007/T008 は [P]。**全 US をブロック**。
- **Phase 3 (US1, P1, MVP)**: Foundational 後。T009→T010→T011(exotic_ev/exotic_recommend 依存)、テスト T012/T013 は [P]。
- **Phase 4 (US2, P1)**: Foundational + **T009(exotic_ev_bets)後**。バックテスト(T016)と baseline(T015)は `exotic_ev_bets` を共有するため US1 の T009 に依存(canonical_field T006 のみでは不足)。T014/T015 [P]→T016、テスト T017/T018/T019 [P]。
- **Phase 5 (US3, P2)**: US1+US2 後(CLI が両方を呼ぶ)。T020→T021、テスト T022 [P]。
- **Phase 6 (Polish)**: 全実装後。

### User Story 独立性

- US1(生成・保存)と US2(バックテスト)は Foundational + **T009(exotic_ev_bets)** 共有後はテストを独立に走らせられる(US2 は T009 に依存)。US3 は両者を CLI で束ねる。

## Parallel 実行例

- Setup: T003 を T001/T002 と並走可。
- Foundational テスト: T007・T008 を並走。
- US1: T012・T013 を並走。
- US2: T014・T015 を並走、テスト T017・T018・T019 を並走。
- Polish: T023・T024・T026 を並走。

## 実装戦略

1. **MVP first**: Phase 1→2→3(US1)で「exotic EV 推奨の生成・保存」を最短達成し、独立にデモ可能。
2. **評価先行(憲法 III)**: Phase 4(US2)で baseline 比較バックテストを追加し採否を判断可能に。
3. **運用性**: Phase 5(US3)で CLI、Phase 6 で lint/全テスト/quickstart 実 DB 検証。
4. 各 Phase の Checkpoint で独立テストを緑にしてから次へ。
