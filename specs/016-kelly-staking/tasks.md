# Tasks: Kelly 賭け金最適化と bankroll backtest

**Input**: Design documents from `specs/016-kelly-staking/`
**Prerequisites**: plan.md, spec.md, research.md (R1–R7), data-model.md, contracts/kelly_recommend.md, contracts/kelly_backtest.md, quickstart.md

**Tests**: 含む（憲法 II リーク / III 評価先行 / IV 確率整合性 / V 監査・再現は必須。pytest + testcontainers + 合成データ）

**Organization**: User story 単位（P1 US1 実オッズ Kelly 推奨 → P1 US2 bankroll backtest → P2 US3 推定オッズ二重疑似抑制）。MVP=US1。

## パス規約

既存 `betting/`(`horseracing-betting`) 拡張。src=`betting/src/horseracing_betting/`、tests=`betting/tests/{unit,integration}/`。
db migration=`db/migrations/versions/`。011/012 の `exotic_selection`(to_selection)・`exotic_ev`(canonical field)・
`exotic_recommend`(永続化)・`exotic_roi`(券種別採点)・`exotic_market`(010) を再利用。確率は P_model のみ(p≠q)、結果非参照。

---

## Phase 1: Setup（スキーマ・スキャフォールド）

- [X] T001 `db/migrations/versions/0006_stake_fraction.py` を作成: `recommendations` に nullable 列 `stake_fraction Numeric` を追加（upgrade/downgrade）。down_revision=0005。`DATABASE_URL=... uv run alembic upgrade head` で適用確認（data-model.md §1）
- [X] T002 [P] `db/src/horseracing_db/models/prediction.py` の `Recommendation` に `stake_fraction: Mapped[Decimal | None] = mapped_column(Numeric)` を追加し、ORM とマイグレーションを一致させる
- [X] T003 [P] `betting/src/horseracing_betting/kelly_types.py` を作成: `KellyConfig`（lambda_real/lambda_est/cap_bet/cap_total/o_min/min_edge/min_edge_est/bankroll/allocation/enable_estimated、既定値は research.md R3）と logic_version エンコード/デコードヘルパ（data-model.md §1 の例形式）を実装する

**Checkpoint**: スキーマ・設定型が揃う。

---

## Phase 2: Foundational（単一買い目 Kelly — 全 US 前提）

**⚠️ 単一買い目の f* 算出（R1/R3）を確定。US1/US2/US3 全てが依存。**

- [X] T004 `betting/src/horseracing_betting/kelly_sizing.py` を作成: 単一買い目 c の `edge=P_model·O−1`、`f*=edge/(O−1)`、実効 fraction=`clip(λ·f*, 0, cap_bet)` を実装。`edge≤min_edge` / `O<o_min` は見送り（fraction=0, 採用しない）。λ は odds 源で `lambda_real`/`lambda_est` を選択（R1/R3）
- [X] T005 [P] `betting/tests/unit/test_kelly_sizing.py` を作成: f* 公式（既知値）、λ・cap_bet クリップ、O_min 除外、edge≤0 見送り、推定時 λ_est 適用を検証（SC-001/SC-005）

**Checkpoint**: 単一 Kelly が単体検証済み。

---

## Phase 3: User Story 1 - 実オッズ Kelly 推奨生成（Priority: P1）🎯 MVP

**Goal**: 実 exotic オッズ(012)優先で P_model×O から Kelly 配分し、bankroll 比例 stake を recommendations に保存。

**Independent Test**: 既知 P_model と実オッズのレースで、各買い目の Kelly fraction（fractional・cap・配分後）が定義どおり、負 edge 見送り、Σ≤cap_total、決定論を検証。

### 実装

- [X] T006 [US1] `betting/src/horseracing_betting/kelly_allocation.py` を作成: 同一(race,bet_type)の採用買い目に対し、`allocation=exact` は期待対数成長 `G(f)=Σ P_model·log(1−S+O·f)+(1−ΣP_model)·log(1−S)` を制約 `f≥0, S=Σf≤cap_total` の下で決定論的に最大化（numpy 凸最適化、乱数なし）。`allocation=heuristic` は個別 f* を合計が cap_total 超なら比例縮小（R2/FR-004）
- [X] T007 [US1] `betting/src/horseracing_betting/kelly_recommend.py` を作成: `exotic_ev` の canonical field（P_model と使用オッズが両方有効、取消・除外を除外し再正規化）で P_model(009)=各買い目的中確率、O=実(012, `exotic_market`/`exotic_odds`)優先・無ければ推定(010)。`kelly_sizing`+`kelly_allocation` で fraction、stake=fraction×bankroll。`exotic_recommend` の永続化経路を拡張し `stake_fraction`・is_estimated_odds・market_odds_used/estimated_market_odds_used・pseudo_odds=1/P_model・pseudo_roi=edge・logic_version を append-only 保存（contracts/kelly_recommend.md）
- [X] T008 [US1] `betting/src/horseracing_betting/cli.py` に `kelly-recommend <race_id>`（--bankroll/--lambda-real/--lambda-est/--cap-bet/--cap-total/--o-min/--allocation/--enable-estimated/--bet-types/--prediction-run）サブパーサを追加する

### US1 テスト

- [X] T009 [P] [US1] `betting/tests/integration/test_kelly_recommend.py` を作成（合成データ）: 実オッズ経路で stake_fraction が定義どおり、負 edge 不保存、Σstake_fraction(券種)≤cap_total、stake_fraction∈[0,cap_bet]、exact と heuristic の差、同一入力2回で完全一致（決定論）、selection JSONB 安全を検証（SC-001/SC-002/SC-003）

**Checkpoint**: US1 単独で動作・テスト緑（MVP）。

---

## Phase 4: User Story 2 - bankroll backtest（Kelly vs flat 採否ゲート）（Priority: P1）

**Goal**: 期間で Kelly stake の bankroll 推移を flat(011/012)と同一条件比較。終端bankroll/対数成長率/最大DD/破産確率/分散/最大連敗。

**Independent Test**: 結果既知の期間で Kelly/flat の6指標を算出、実/二重疑似分離、success=リスク調整後成長で優位を検証。

### 実装

- [X] T010 [US2] `betting/src/horseracing_betting/kelly_backtest.py` を作成: walk-forward 時系列順に各レースで買い目生成（kelly_recommend と同一ロジック、結果非参照）→ `exotic_roi` の券種別的中（順序/無順序/包含、複勝・ワイドは複数当たりベット単位、009 field-size）→ 払戻=実 exotic present なら実、無ければ O_est、的中 +stake·(O−1)/外れ −stake/同着按分 → bankroll 更新、ruin 閾値割れで停止。終端bankroll/対数成長率/最大DD/分散/最大連敗/件数/的中率/見送り率を算出（contracts/kelly_backtest.md, data-model.md §3）
- [X] T011 [US2] `betting/src/horseracing_betting/kelly_backtest.py` に破産確率推定を追加: 実経路 ruin(0/1) + block bootstrap（時系列ブロック保持リサンプリング、固定ブロック化で決定論）で ruin 割合。実区間/二重疑似区間を分離集計。flat と同一条件比較し success=リスク調整後成長で優位（ROI>1 単独不可）を判定（R6/FR-013/FR-014/FR-015）
- [X] T012 [US2] `betting/src/horseracing_betting/cli.py` に `kelly-backtest --from --to`（--bankroll/--ruin-threshold/--lambda-*/--cap-*/--o-min/--allocation/--bootstrap-blocks/--bet-types/--compare flat）サブパーサを追加する

### US2 テスト

- [X] T013 [P] [US2] `betting/tests/integration/test_kelly_backtest.py` を作成（合成データ）: Kelly/flat の6指標が両戦略で算出、同一条件比較、実/二重疑似分離集計、ruin 停止、block bootstrap 決定論、success がリスク調整後成長で判定（単なる ROI>1 で success にしない）を検証（SC-006/SC-007/SC-008）

**Checkpoint**: US2 単独で動作・テスト緑。採否ゲート成立。

---

## Phase 5: User Story 3 - 推定オッズ Kelly の二重疑似ラベルと安全抑制（Priority: P2）

**Goal**: 実オッズ欠損の買い目は推定(010)由来 Kelly を double_pseudo 標識し、λ_est・フィルタで保守的に抑制。

**Independent Test**: 推定オッズのみの買い目で double_pseudo=true・is_estimated_odds=true、λ_est<λ_real で実より保守的、低オッズ/低edge フィルタを検証。

### 実装

- [X] T014 [US3] `betting/src/horseracing_betting/kelly_recommend.py` / `kelly_sizing.py` に推定オッズ安全経路を実装: 推定オッズ買い目は `lambda_est`・`min_edge_est`・`o_min` を適用し、`enable_estimated=false` で完全無効化可。保存行は is_estimated_odds=true（=double_pseudo、API 導出と同一規約）。backtest にも二重疑似ラベルを伝播（R3/FR-005/FR-006）

### US3 テスト

- [X] T015 [P] [US3] `betting/tests/integration/test_kelly_estimated.py` を作成（合成データ）: 推定のみ買い目が is_estimated_odds=true、同一買い目で実オッズ仮定時より stake_fraction が同等以下（λ_est<λ_real）、min_edge_est/o_min による除外、enable_estimated=false で生成ゼロ、backtest の二重疑似分離を検証（SC-004/SC-005）

**Checkpoint**: 全 P1+P2 完了。Kelly 推奨・backtest・推定抑制が誤読防止ラベル付きで成立。

---

## Phase 6: Polish & Cross-Cutting

- [X] T016 [P] `betting/tests/integration/test_kelly_leak_guard.py` を作成（憲法 II）: stake_fraction/オッズ/q/Kelly fraction が `features/` `training/` の入力に出現しないこと（import グラフ/文字列走査）、買い目生成が `race_results` を参照しないこと（結果は採点のみ）を assert（SC-010）
- [X] T017 [P] `betting/tests/unit/test_kelly_consistency.py` を作成（憲法 IV）: P_model が 009 canonical field（取消・除外を除外し再正規化）に基づくこと、Σstake_fraction≤cap_total が任意入力で破れないこと（SC-002）
- [X] T018 [P] `betting/tests/integration/test_kelly_determinism.py` を作成: kelly-recommend と kelly-backtest（block bootstrap 含む）が同一入力で完全一致（SC-003）
- [X] T019 `specs/016-kelly-staking/quickstart.md` を実行: migration 0006 適用 → 実 DB で kelly-recommend（実オッズあるレース）+ 短期間 kelly-backtest を実データスモーク（実/二重疑似ラベル・6指標・flat比較を目視、[[local-db-setup]] の horseracing DB）
- [X] T020 [P] `CLAUDE.md` に 016 の 1 行サマリを追記する（011–015 と同形式: Kelly 式・相互排他配分・p≠q・二重疑似抑制・破産確率評価・stake_fraction 列を要約）

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001→T002[P]/T003[P]。
- **Phase 2 (Foundational)**: Setup 後。T004→T005[P]。**全 US をブロック**（単一 Kelly）。
- **Phase 3 (US1, MVP)**: Foundational 後。T006→T007→T008、テスト T009[P]。
- **Phase 4 (US2)**: US1（kelly_recommend ロジック）後。T010→T011→T012、テスト T013[P]。
- **Phase 5 (US3)**: US1 後（推奨経路に推定分岐を追加）。T014、テスト T015[P]。US2 とは独立に着手可（backtest への伝播は T011 完了後）。
- **Phase 6 (Polish)**: 全実装後。T016/T017/T018/T020[P]、T019。

### User Story 独立性

- US1 は実オッズ Kelly 推奨で独立（MVP）。US2 は backtest harness（US1 のロジックを期間適用）。US3 は推定オッズ分岐（US1 の拡張）。US2/US3 は US1 後に並行着手可。

## Parallel 実行例

- Setup: T002/T003 を並走。
- 各 US テストは [P]（T009/T013/T015）。
- Polish: T016/T017/T018/T020 を並走。

## 実装戦略

1. **MVP first**: Phase 1→2→3（US1）で「実オッズ Kelly 推奨 + stake_fraction 永続化」を最短達成。
2. **評価ゲート**: US2 で bankroll backtest（Kelly vs flat、破産確率）を成立させ採否判断可能に。
3. **誤読防止**: US3 で推定オッズ二重疑似の安全抑制・ラベルを機構化。
4. 各 Checkpoint で独立テスト緑。憲法 II（リーク・p≠q）/ III（評価先行・baseline比較）/ IV（確率整合）/ V（stake_fraction+logic_version で監査・再現）を全タスクで維持。
