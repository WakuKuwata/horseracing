---
description: "Task list for 単勝 EV 推奨と疑似ROIバックテスト"
---

# Tasks: 単勝 EV 推奨と疑似ROIバックテスト

**Input**: Design documents from `specs/007-win-bet-recommendation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 含む。spec の Independent Test と憲法 II/III/IV/V のため test タスクを生成する。
**結果非参照(リーク境界)・除外/再正規化・疑似ROI 採点(取消/DNF/同着)が最重要テスト**。

**Source of truth**: 疑似評価・母集団/再正規化・ROI 採点・baseline・成功基準・selection 契約は research.md /
data-model.md / contracts/。予測は Feature 006、確率整合性は憲法 IV。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(異なるファイル・依存なし)
- パスはリポジトリ root 基準。betting パッケージは `betting/`

---

## Phase 1: Setup

- [X] T001 `betting/` のディレクトリ構成を plan.md 通りに作成(`betting/src/horseracing_betting/`, `betting/tests/{unit,integration}/`)
- [X] T002 `betting/pyproject.toml` を作成し依存定義(`horseracing-db`/`horseracing-features`/`horseracing-eval`/`horseracing-serving` をパス依存、numpy, pandas, sqlalchemy>=2.0。dev: pytest, testcontainers[postgres], ruff)
- [X] T003 [P] `betting/pyproject.toml` に ruff 設定と `[tool.pytest.ini_options]`(integration マーカー、tests E501 ignore)+ `__init__.py`(`BETTING_LOGIC_VERSION`)を追加

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 完了までユーザーストーリー着手不可

- [X] T004 `betting/src/horseracing_betting/ev.py`: `Bet` + `select_ev_bets`(started のみ→odds null/<=0・win_prob<=0 除外→残存馬 win_prob を Σ=1 再正規化→`EV=win_prob×odds`→`EV>=閾値` を全頭。**結果非参照**)(contracts/recommend.md, INV-B1〜B4)
- [X] T005 `betting/tests/conftest.py`: testcontainers PostgreSQL16 + `db/` alembic head + session + テスト間 truncate + 合成データ(レース/出走/結果/odds)+ active モデル + prediction_run/race_predictions を作るヘルパ

**Checkpoint**: 基盤完成(EV 選択コア・テスト基盤)

---

## Phase 3: User Story 1 - 単勝 EV 買い目を生成して保存 (Priority: P1) 🎯 MVP

**Goal**: prediction_run/レース指定で EV>=閾値 の単勝買い目を recommendations に保存。

**Independent Test**: ある prediction_run で推奨生成し、EV>=閾値 の馬だけが `bet_type='win'` で保存され、各行に
監査情報(market_odds_used/pseudo_odds/pseudo_roi/selection/logic_version)が揃う。除外・再正規化が効く。

### Tests for User Story 1 ⚠️

- [X] T006 [P] [US1] ユニット: `select_ev_bets` が取消・除外/odds null<=0/win_prob=0 を除外し残存馬で再正規化、`EV>=閾値` のみ返す、結果(着順)を参照しない、決定論 — `betting/tests/unit/test_ev_select.py`
- [X] T007 [P] [US1] 統合: `generate_recommendations` が EV>=閾値 の馬を `recommendations`(bet_type='win', selection={horse_id,horse_number}, market_odds_used, is_estimated_odds=false, pseudo_odds=1/p, pseudo_roi=p*odds-1, logic_version)に append-only 保存、再生成で新群 — `betting/tests/integration/test_recommend.py`

### Implementation for User Story 1

- [X] T008 [US1] `betting/src/horseracing_betting/recommend.py`: `generate_recommendations(session, prediction_run_id, threshold, stake, logic_version)`(race_predictions + race_horses(odds/horse_number/entry_status)結合 → `select_ev_bets` → recommendations 保存。logic_version 既定構成)
- [X] T009 [US1] `betting/src/horseracing_betting/cli.py` + `__main__.py`: `recommend --prediction-run/--race-id --threshold --stake`(保存件数・各 EV 表示)

**Checkpoint**: US1 単独で EV 買い目生成 → recommendations 保存が成立(MVP の中核)

---

## Phase 4: User Story 2 - 期間の疑似ROIバックテストで baseline と比較 (Priority: P1)

**Goal**: 期間の EV 戦略を ROI baseline(人気1番/均等)と同一条件で疑似ROI 比較。

**Independent Test**: 合成データで EV 戦略と 2 baseline を同一レース集合で走らせ、回収率/的中率/見送り率/最大DD/
最大連敗が定義どおり(勝ち/負け/DNF/取消/同着)計算され、全レポートが pseudo。

### Tests for User Story 2 ⚠️

- [X] T010 [P] [US2] ユニット(最重要): 疑似ROI 採点が 勝ち/負け/**DNF=負け**/**取消・除外=母集団除外(負けに数えない)**/**同着1着=的中** を正しく扱い、回収率/的中率/見送り率/最大DD(賭けたレースのみの累積損益 Σbet_pnl の絶対額 DD)/最大連敗を定義どおり算出 — `betting/tests/unit/test_roi_scoring.py`
- [X] T011 [P] [US2] ユニット: `FavoriteROIBaseline`(最低 odds 1 頭)/ `UniformROIBaseline`(全頭均等)/ `EVStrategy` が started のみ・odds null<=0 除外で買い目を返し、同一レース集合で比較できる、決定論 — `betting/tests/unit/test_strategies.py`

### Implementation for User Story 2

- [X] T012 [US2] `betting/src/horseracing_betting/strategies.py`: `Strategy` プロトコル + `EVStrategy`/`FavoriteROIBaseline`/`UniformROIBaseline`(contracts/backtest.md)
- [X] T013 [US2] `betting/src/horseracing_betting/roi.py`: `RoiReport` + `score_backtest`(payout/的中/回収率/見送り率/最大DD/最大連敗、`pseudo=True`、R3/FR-007/009)
- [X] T014 [US2] `betting/src/horseracing_betting/backtest.py`: `run_backtest(session, start_date, end_date, model_version, threshold, stake)`(serving 純部品で in-memory 予測 → build_feature_matrix 期間 1 度 → 各レース集約 → 3 戦略を同一集合で score → `{name: RoiReport}`)

**Checkpoint**: US1+US2 = 推奨生成 + 疑似ROIバックテスト比較が完成(評価先行の到達点)

---

## Phase 5: User Story 3 - CLI でバックテスト実行 (Priority: P2)

**Goal**: 期間指定の疑似ROIバックテストを CLI から実行し、戦略 vs baseline を表示。

**Independent Test**: `backtest --from --to --threshold --stake` で EV 戦略と 2 baseline の疑似ROI 指標が表で表示、
閾値/stake で結果が変わる。

### Tests for User Story 3 ⚠️

- [X] T015 [P] [US3] 統合: 実 DB(合成)で `run_backtest` が EV/Favorite/Uniform の RoiReport を返し、同一レース集合・同一 stake で比較される、全 pseudo — `betting/tests/integration/test_backtest.py`

### Implementation for User Story 3

- [X] T016 [US3] `cli.py` に `backtest --from --to --threshold --stake [--model-version]` サブコマンド(戦略 vs baseline の疑似ROI 表 + pseudo 明示を表示)

**Checkpoint**: US1+US2+US3 = 推奨生成 + バックテスト + CLI が完成

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T017 [P] `betting/README.md` を作成(概要・CLI・テスト・疑似評価明示・リーク境界・除外/再正規化・ROI baseline・成功基準)
- [X] T018 ruff クリーン + 全テスト green を確認(`betting/`: `uv run ruff check`, `uv run pytest`)
- [X] T019 (ローカル・任意) 実データ(active モデル + 2008 取込)で `recommend` と `backtest --from 2008-01-01 --to 2008-12-31` を実行し、recommendations 保存・疑似ROI 指標・baseline 比較・pseudo 明示を確認

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK(EV コア・conftest)
- **US1 (Phase 3)**: Foundational 後。推奨生成 + 保存(MVP)
- **US2 (Phase 4)**: Foundational(EV コア)後。バックテスト + baseline。US1 と概ね独立(recommend.py を共有しない)
- **US3 (Phase 5)**: US2(backtest)に CLI を追加
- **Polish (Phase 6)**: 望むストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後。recommendations 保存
- **US2 (P1)**: Foundational 後。EVStrategy は ev.py を再利用。US1 非依存(並行着手可)
- **US3 (P2)**: US2 の後

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- **結果非参照(T006)・疑似ROI 採点(T010)を最優先で固定**
- ev(基盤)→ recommend/strategies → roi → backtest → cli の順

### Parallel Opportunities

- Setup の T003、各ストーリーの test タスク [P] は並列可
- US1(recommend.py)と US2(strategies/roi/backtest.py)は異なるファイルで並行可
- Polish の T017 は並列可

---

## Implementation Strategy

### MVP First (US1 + US2 = P1)

1. Setup → Foundational(EV コア・conftest)
2. US1: EV 買い目生成 + recommendations 保存(除外・再正規化・監査)
3. US2: 疑似ROIバックテスト + baseline 比較(評価先行)
4. ここで「予測→買い目→疑似ROI で baseline 比較」の完全ループが完成

### Incremental Delivery

1. Setup + Foundational
2. US1 → 推奨生成 + 保存
3. US2 → 疑似ROIバックテスト + baseline(MVP 完成)
4. US3 → バックテスト CLI
5. Polish → README・実データスモーク

---

## Notes

- [P] = 異なるファイル・依存なし
- **疑似評価の明示が本 feature の前提**(確定オッズ closing-oracle)。全 RoiReport `pseudo=True`、logic_version/README に明記
- **リーク境界**: 買い目選択は win_prob×odds のみ、`race_results`(着順)非参照(T006 で検査)。結果は採点のみ
- **母集団**: 取消・除外を除外して残存馬 win_prob を再正規化(憲法 IV、T006)
- ROI baseline は確率品質 baseline(Feature 003)と別物(Favorite/Uniform を新設)
- 成功条件=baseline 超え(SC-004)。`回収率>1.0` は参考バー(SC-007、控除率考慮)
- スキーマ変更なし。複勝・馬連・三連複(結合確率)・推定オッズは将来(P0)
