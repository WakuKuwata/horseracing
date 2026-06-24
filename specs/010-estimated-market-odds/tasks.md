---
description: "Task list for 推定市場オッズ変換"
---

# Tasks: 推定市場オッズ変換 (Estimated Market Odds Conversion)

**Input**: Design documents from `specs/010-estimated-market-odds/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 含む。spec の Independent Test と憲法 II/III/V のため test タスクを生成する。
**単勝復元 golden・p/q 分離(p 非参照)・q 整合性・派生オッズ cap が最重要テスト**。

**Source of truth**: 市場含意 q・単勝復元・PL 外挿の疑似性・控除率・cap・評価は research.md / data-model.md /
contracts/。結合確率エンジンは Feature 009、metrics は Feature 003。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(異なるファイル・依存なし)
- パスはリポジトリ root 基準。確率パッケージは `probability/`(既存を拡張)

---

## Phase 1: Setup

- [X] T001 既存 `probability/` パッケージ構成を確認(新パッケージ不要)。`probability/__init__.py` に `MARKET_LOGIC_VERSION` を追加し、market 系モジュール/テストの置き場(`src/horseracing_probability/`, `tests/{unit,integration}/`)を確認

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 完了までユーザーストーリー着手不可

- [X] T002 `probability/src/horseracing_probability/market_odds.py` の基盤: `DEFAULT_PAYOUT_RATES`(単複0.80/馬連ワイド0.775/馬単三連複0.75/三連単0.725)+ `MarketOddsError` + `market_implied_win_probs(win_odds)`(有効オッズ>0 のみ母集団、`q_i=(1/odds_i)/Σ(1/odds_j)`、Σ≤0/残存不足は error。**q は投票シェア=p ではない**)(contracts/market_odds.md, INV-M2/M6, R1)

**Checkpoint**: 基盤完成(控除率・市場含意 q)

---

## Phase 3: User Story 1 - 単勝オッズから各券種の推定市場オッズを導出 (Priority: P1) 🎯 MVP

**Goal**: 単勝オッズ → q → 009 エンジン → P_market → 控除率 → 推定オッズ。

**Independent Test**: 人工オッズ `odds=R/s` で `q=s`・推定単勝オッズ=odds を厳密復元。q を 009 に通した各券種の推定
オッズが整合的に得られ、p を一切参照しない。

### Tests for User Story 1 ⚠️

- [X] T003 [P] [US1] ユニット(最重要): 人工オッズ `odds_i=R/s_i` で `market_implied_win_probs` が `q_i=s_i`(Σq=1)、推定単勝オッズ `=R/q_i=odds_i` を **atol=1e-9 で厳密復元**(SC-001)。`R·S=1` で復元、`R·S≠1` で全馬同率誤差 `R·S` — `probability/tests/unit/test_market_recovery.py`
- [X] T004 [P] [US1] ユニット(最重要・リーク検査): `estimate_market_odds` が q を 009 に通し各券種の整合性(`Σ馬単=1`・`Σ三連単=1`・`wide=Σ_k trio`)を満たす、推定オッズ=`(1−takeout_b)/P_market`、控除率が出力に残り **`is_estimated is True`**。**入力にモデル p を一切使わない**(market odds のみ=リーク境界、SC-002/SC-005)— `probability/tests/unit/test_estimate_odds.py`
- [X] T005 [P] [US1] ユニット: オッズ欠損/0/負・取消(母集団外)を除外して q 再正規化、推定不能(MarketOddsError)。`P_market<=eps` で推定オッズ **None**、それ以外は `min(R/P, odds_cap)`(既定 odds_cap=10000)・**確率本体は cap しない**。複勝の頭数依存(5–7=top2/8+=top3/≤4=None)・小頭数。**決定論(同一入力で同一出力を assert)**(SC-003/006)— `probability/tests/unit/test_market_edge.py`

### Implementation for User Story 1

- [X] T006 [US1] `market_odds.py`: `EstimatedOdds` dataclass + `estimate_market_odds(win_odds, field_size, payout_rates, odds_cap)`(q → `joint_probabilities(q)`(009)→ 各券種 `(1−takeout_b)/P_market` → cap/None。`is_estimated=True`、payout_rates 監査)(FR-002/003/005, R3/R5/R6)

**Checkpoint**: US1 単独で推定市場オッズ導出が成立(MVP の核、憲法 P0)

---

## Phase 4: User Story 2 - 変換規則を過去データで検証 (Priority: P1)

**Goal**: 単勝オッズ復元誤差 + 市場含意 q の校正を過去データで評価(疑似明示)。

**Independent Test**: 過去レースで復元誤差(レース単位)と q の NLL/Brier が算出され、全出力が疑似評価明示。

### Tests for User Story 2 ⚠️

- [X] T007 [P] [US2] ユニット: `recover_win_odds`(`hat_odds_i=R_win/q_i`)と pure な復元/校正集計関数が、合成データで `R·S=1` のとき復元誤差≈0、q の NLL/Brier を算出 — `probability/tests/unit/test_market_validation.py`
- [X] T008 [P] [US2] 統合: 実 DB(合成 race_horses.odds + race_results)で `evaluate_market_odds` が RecoveryReport + QCalibrationReport を返し、全 `pseudo=True`、変換が結果/モデル p 非参照(SC-004/007)— `probability/tests/integration/test_market_validation_db.py`

### Implementation for User Story 2

- [X] T009 [US2] `probability/src/horseracing_probability/market_calibration.py`: `RecoveryReport`/`QCalibrationReport` + `recover_win_odds` + `evaluate_market_odds(session, start_date, end_date, payout_rates)`(started+有効オッズ馬の win_odds→復元誤差、q→勝馬の NLL/Brier。`eval.metrics` 流用。全 pseudo)(contracts/validation.md, FR-009, R7)

**Checkpoint**: US1+US2 = 推定変換 + 検証(評価先行)完成

---

## Phase 5: User Story 3 - レースの推定オッズを CLI で表示 (Priority: P2)

**Goal**: レース指定で各券種の推定オッズ上位 K + 控除率・推定明示を表示。

**Independent Test**: race_id 指定で各券種の推定オッズ上位 K と控除率・「推定」明示が表示される。

### Tests for User Story 3 ⚠️

- [X] T010 [P] [US3] 統合: CLI `estimate-odds` が race_horses.odds から各券種の推定オッズ上位 K + 控除率 + **「推定」明示(出力に "推定"/estimated 文字列)を assert**。`validate-odds` が復元/校正レポート(**"pseudo"/"疑似" 明示**)を表示 — `probability/tests/integration/test_cli_market.py`

### Implementation for User Story 3

- [X] T011 [US3] `probability/src/horseracing_probability/cli.py` に `estimate-odds --race-id --top K`(race_horses.odds→estimate_market_odds→上位表示 + 推定明示)+ `validate-odds --from --to`(evaluate_market_odds 表示)を追加

**Checkpoint**: US1+US2+US3 = 変換 + 検証 + CLI が完成

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T012 [P] `probability/README.md` に推定市場オッズ節を追記(q=投票シェア≠モデル p、単勝復元、PL 外挿の疑似性、控除率設定可能+logic_version、cap、p/q 分離、評価、exotic EV/永続化は将来)
- [X] T013 ruff クリーン + 全テスト green を確認(`probability/`: `uv run ruff check`, `uv run pytest`)
- [X] T014 (ローカル・任意) 実データ(2008 取込、race_horses.odds)で `estimate-odds`(単勝復元の健全性)と `validate-odds`(復元誤差・q 校正、疑似明示)を実行

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK(控除率・市場含意 q)
- **US1 (Phase 3)**: Foundational 後。推定オッズ導出の中核(MVP、憲法 P0)
- **US2 (Phase 4)**: US1(market_odds)を使って検証
- **US3 (Phase 5)**: US1/US2 を CLI で公開
- **Polish (Phase 6)**: 望むストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後。market_odds(009 を q 入力で再利用)
- **US2 (P1)**: US1 の market_odds/q に依存
- **US3 (P2)**: US1/US2 の後

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- **単勝復元(T003)・p 非参照/q 整合性(T004)・cap/再正規化(T005)を最優先で固定**
- market_implied_win_probs(基盤)→ estimate_market_odds → market_calibration → cli の順

### Parallel Opportunities

- 各ストーリーの test タスク [P] は並列可
- US1 の test(T003/T004/T005)は並列可。実装は順次(market_odds.py)
- Polish の T012 は並列可

---

## Implementation Strategy

### MVP First (US1 = P1 MVP)

1. Setup → Foundational(控除率・市場含意 q)
2. US1: q → 009 → P_market → 推定オッズ(復元性・整合性・p 非参照)→ 憲法 P0「推定市場オッズ変換規則」完成
3. ここで exotic オッズの推定基盤が完成(009 のモデル確率と組み合わせた exotic EV の前提)

### Incremental Delivery

1. Setup + Foundational
2. US1 → 推定市場オッズ変換(MVP)
3. US2 → 単勝復元 + q 校正(評価先行)
4. US3 → CLI
5. Polish → README・実データスモーク

---

## Notes

- [P] = 異なるファイル・依存なし
- **codex 市場モデルレビューが核**: ①q=投票シェア(真の勝率/モデル p ではない)②p 非参照・p/q 分離 ③単勝復元
  `R·S·odds_i` ④PL 外挿 exotic は実価格と乖離→**推定/疑似明示** ⑤控除率設定可能+logic_version ⑥`P→0` は派生オッズ cap
  (確率本体は cap しない)
- リーク境界: 変換は市場オッズのみ(モデル p 非参照)。検証のみ結果を採点に使う
- スキーマ変更なし。exotic EV/推奨・推定オッズ永続化・実 exotic オッズ取得・bias 補正・複勝厳密モデルは将来
