# Tasks: モデル確率校正と edge haircut による Kelly 過大賭け抑制

**Input**: Design documents from `specs/017-model-calibration/`
**Prerequisites**: plan.md, spec.md, research.md (R1–R7), data-model.md, contracts/calibrate_eval.md, contracts/calibrated_kelly.md, quickstart.md

**Tests**: 含む（憲法 II リーク / III 評価先行 / IV 確率整合 / V 監査は必須。pytest + testcontainers + 合成データ）

**Organization**: User story 単位（P1 US1 校正器の学習・評価 → P1 US2 校正+haircut Kelly とリスク比較 → P2 US3 p×q 2×2 整合）。MVP=US1。

## パス規約

`probability/`（校正器・評価）+ `betting/`（haircut・Kelly 統合・比較）拡張。**スキーマ変更なし**（校正情報は
logic_version、016 の `stake_fraction` 再利用）。013 の `fl_bias`(_golden_min/_engine_normalize)・009 engine・
011 canonical_field・016 Kelly を再利用。確率は p 系統のみ（p≠q、market 側に戻さない）、校正器は結果非参照。

---

## Phase 1: Setup（haircut 設定・スキャフォールド）

- [X] T001 [P] `betting/src/horseracing_betting/kelly_types.py` の `KellyConfig` に `haircut_type: str = "none"`（none/relative/absolute）と `haircut: float = 0.0` を追加し、`kelly_logic_version` に haircut を含める（data-model.md §6, R4）
- [X] T002 [P] `probability/src/horseracing_probability/model_calibration.py` を新規作成（空骨子 + module docstring: p 校正は p 系統のみ、結果非参照、walk-forward、p≠q）

**Checkpoint**: 設定・モジュール枠が揃う。

---

## Phase 2: Foundational（p 校正器 fit/apply — 全 US 前提）

**⚠️ p 校正器（power/temperature）と canonical field への適用を確定。US1/US2/US3 全てが依存。**

- [X] T003 `probability/src/horseracing_probability/model_calibration.py` に `PCalibrator`（method/params/train_window/n_races/n_samples/prob_range/select/base_model_version/sufficient/logic_version）と `fit_p_calibrator(samples, *, method="power", min_races, min_wins, train_window, base_model_version)` を実装。power は `p'∝p^γ`、γ を normalized winner-NLL の golden-section MLE（013 の `_golden_min`/`fit_power_gamma` を p に転用、決定論）。min_races/min_wins 未達 → identity(γ=1) fallback（sufficient=False）（R1/R3, FR-001/FR-002/FR-007）
- [X] T004 `probability/src/horseracing_probability/model_calibration.py` に `apply_p_calibrator(p: dict, calibrator) -> dict`（`p'_i∝p_i^γ` をレース内正規化 + 009 engine-consistent clip = `_engine_normalize`、評価==使用ベクトル）を実装（R1, FR-004）
- [X] T005 [P] `probability/tests/unit/test_model_calibration.py` を作成: power fit が既知 γ を回復、apply が Σ=1 で engine-consistent、γ<1 で過信緩和（最大 p 低下）、ranking 保存、min_races 未達で identity fallback、決定論を検証（SC-003/SC-004）

**Checkpoint**: p 校正器が単体検証済み。

---

## Phase 3: User Story 1 - 校正器の学習・評価（Priority: P1）🎯 MVP

**Goal**: walk-forward で校正器を学習し、生 p vs 校正 p' の NLL/Brier/ECE/reliability（+overconfidence 指標、009 後 joint reliability）を比較、採用判定。

**Independent Test**: 対象レース前のみで fit、out-of-sample で 生 p/校正 p' の指標を算出、選択も窓内、結果非参照を検証。

### 実装

- [X] T006 [US1] `probability/src/horseracing_probability/model_calibration.py` に walk-forward サンプルローダ（013 の `race_before`/`load_samples` を p 用に転用: 対象レース開始より厳密前の (p, winner)。同着除外+件数。`_latest_run_predictions` 相当でモデル p 取得）を実装（R3, FR-002/FR-008）
- [X] T007 [US1] `probability/src/horseracing_probability/model_calibration.py` に `evaluate_p_vs_pprime(samples, calibrator, *, bins)` を実装: 生 p / 校正 p' の NLL・Brier・ECE・reliability（overall + 人気帯別 over/under・reliability slope・calibration-in-the-large）。`PCalibrationReport` を返す。**選択（方式/ハイパラ）は各 fold 学習窓内**で行う（選択リーク禁止）（R5, FR-003/FR-010, SC-001/SC-002）
- [X] T008 [US1] `probability/src/horseracing_probability/model_calibration.py` に joint reliability 評価（校正 p' を 009 に通し券種別 exacta/trifecta の winner NLL/Brier を before/after）を実装し、**marginal 改善だが joint 悪化を検出**（R2, FR-005, SC-005）
- [X] T009 [US1] `probability/src/horseracing_probability/cli.py` に `calibrate-eval --from --to [--method --select --min-races --min-wins --bands --model-version]` サブパーサ + ハンドラを追加（contracts/calibrate_eval.md）

### US1 テスト

- [X] T010 [P] [US1] `probability/tests/integration/test_calibrate_eval_db.py` を作成（合成データ）: walk-forward で校正器が対象レース結果を読まない、p vs p' の NLL/Brier/ECE/reliability 算出、人気帯別 over/under、009 後 joint reliability before/after、同着除外件数、同一入力で完全一致（決定論）を検証（SC-001/SC-002/SC-003/SC-005）

**Checkpoint**: US1 単独で動作・テスト緑（MVP、採用ゲート成立）。

---

## Phase 4: User Story 2 - 校正 + haircut 適用 Kelly とリスク比較（Priority: P1）

**Goal**: 校正済み P_model' と edge haircut を 016 Kelly に opt-in 適用、生 Kelly と同一条件で比較し過大賭け低減（最大DD/破産確率/分散）を検証。

**Independent Test**: 同一期間で raw/cal/cal+haircut を比較、Kelly リスク非悪化を必須ガード、逆転・過剰保守を検出。

### 実装

- [X] T011 [US2] `betting/src/horseracing_betting/kelly_sizing.py` の `single_kelly` に edge haircut を適用: `haircut_type=relative` は `edge_adj=(1−h)·edge`、`absolute` は `edge−h`、`none` は素通し。`edge_adj` で f* を計算、`edge_adj≤min_edge` は見送り（R4, FR-006）
- [X] T012 [US2] `betting/src/horseracing_betting/kelly_recommend.py` に `p_calibrator=None` opt-in を追加: canonical field 構築後 `apply_p_calibrator` で p→p'（無指定は生 p、後方互換）→ 009/Kelly。logic_version に校正方式/γ/窓/選択/haircut/base_model_version を追記（R7, FR-011/FR-014）
- [X] T013 [US2] `betting/src/horseracing_betting/kelly_backtest.py` に `p_calibrator=None` opt-in を追加し、`_placed_bets_for_race` の field p を p' に伝播（生 p は後方互換）（R6, FR-011）
- [X] T014 [US2] `betting/src/horseracing_betting/calibration_eval.py` を新規作成: `compare_calibration_modes(session, ..., modes=("raw","cal","cal+haircut"), p_calibrator, cfg)` が 016 の bankroll backtest を同一条件で mode 別実行し、6 指標（終端bankroll/対数成長率/最大DD/破産確率/分散/最大連敗）+ `risk_not_worse`（生 Kelly 比 最大DD・破産確率 非悪化）+ `over_conservative`（成長過削り）+ verdict を返す（R6, FR-012, SC-006/SC-007/SC-008）
- [X] T015 [US2] `betting/src/horseracing_betting/cli.py` に `kelly-calibration-compare --from --to --modes [--haircut-type --haircut --p-window --model-version]` と、`kelly-recommend` への `--p-calibrator`/`--haircut-type`/`--haircut` フラグを追加（contracts/calibrated_kelly.md）

### US2 テスト

- [X] T016 [P] [US2] `betting/tests/unit/test_edge_haircut.py` を作成: relative/absolute haircut が edge を縮小、`edge_adj≤0` 見送り、`none` で素通し、校正と独立に効くことを検証（FR-006）
- [X] T017 [P] [US2] `betting/tests/integration/test_calibrated_kelly.py` を作成（合成データ）: p_calibrator 適用で logic_version に校正情報、生 p 経路の後方互換（無指定で 016 と一致）、校正で stake_fraction が変化、決定論を検証（SC-010）
- [X] T018 [P] [US2] `betting/tests/integration/test_kelly_calibration_compare.py` を作成（合成データ）: raw/cal/cal+haircut の 6 指標算出、success=校正改善 かつ Kelly リスク非悪化（必須ガード）、校正改善だが Kelly 悪化の逆転を明示、過剰保守検出を検証（SC-006/SC-007/SC-008）

**Checkpoint**: US2 単独で動作・テスト緑。校正の運用価値（Kelly 安全化）を測定可能。

---

## Phase 5: User Story 3 - p×q 両側校正の 2×2 整合（Priority: P2）

**Goal**: p 校正（本）と q 校正（013）併用時の二重補正を 2×2（raw/cal p × raw/cal q）で検出、順序 q→O_est→p を固定。

**Independent Test**: 4 通りの EV・edge 分布・Kelly リスクを比較、edge 過縮小を検出、p 校正が market 側に戻らないことを検証。

### 実装

- [X] T019 [US3] `betting/src/horseracing_betting/calibration_eval.py` に `compare_pq_grid(session, ..., p_calibrator, q_calibrator)` を追加: raw/cal p × raw/cal q の 2×2 で EV・edge 分布（中央値/分散）・Kelly リスクを算出。**順序 = q 校正(013)で O_est 確定 → p 校正 P_model' と結合**、p 校正結果を 010 推定側に渡さない（R6, FR-013, SC-009）

### US3 テスト

- [X] T020 [P] [US3] `betting/tests/integration/test_pq_grid.py` を作成（合成データ）: 2×2 の 4 セルが算出され、両側校正で edge 分布が縮む（二重補正）を検出、p 校正器が q/O_est 計算経路に出現しない（p≠q）ことを検証（SC-009）

**Checkpoint**: 全 P1+P2 完了。p 校正・haircut・013 併用の整合が測定可能。

---

## Phase 6: Polish & Cross-Cutting

- [X] T021 [P] `betting/tests/integration/test_calibration_leak_guard.py` を作成（憲法 II）: 校正済み p'・haircut・調整後 edge・Kelly fraction が `features/` `training/` の入力に出現しないこと（文字列/import 走査）、校正適用 Kelly 生成が `race_results` を参照しないことを assert（SC-002）
- [X] T022 [P] `probability/tests/integration/test_calibrate_walk_forward.py` を作成（憲法 II）: 校正器が対象レース開始より厳密前のみ学習（race_before）、方式・ハイパラ選択も学習窓内（評価窓を見ない）、小データ fold で identity fallback を検証（FR-002/FR-003/FR-007, SC-002）
- [X] T023 `specs/017-model-calibration/quickstart.md` を実行: 実 DB で `calibrate-eval`（校正品質 + joint reliability）+ `kelly-calibration-compare`（生 vs 校正+haircut）を実データスモーク（[[local-db-setup]] の horseracing DB、スキーマ変更なし）
- [X] T024 [P] `CLAUDE.md` に 017 の 1 行サマリを追記（011–016 と同形式: p 校正(power/temperature)・joint 非保証ゲート・選択も窓内+fallback・校正/haircut 役割分離・2×2(p×q)・NLL/Brier 主+Kelly 非悪化必須ガード・スキーマ変更なしを要約）

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001[P]/T002[P]。
- **Phase 2 (Foundational)**: Setup 後。T003→T004、テスト T005[P]。**全 US をブロック**（校正器 fit/apply）。
- **Phase 3 (US1, MVP)**: Foundational 後。T006→T007→T008→T009、テスト T010[P]。
- **Phase 4 (US2)**: Foundational 後（US1 と独立だが評価観点で並行可）。T011→T012→T013→T014→T015、テスト T016/T017/T018[P]。
- **Phase 5 (US3)**: US2（calibration_eval）後。T019、テスト T020[P]。
- **Phase 6 (Polish)**: 全実装後。T021/T022/T024[P]、T023。

### User Story 独立性

- US1 は校正器の学習・評価で独立（MVP、採用ゲート）。US2 は校正+haircut の Kelly 適用・比較（Foundational に依存、US1 とは評価軸が別）。US3 は US2 の比較ハーネスに 2×2 を足す。

## Parallel 実行例

- Setup: T001/T002 を並走。Foundational テスト T005[P]。
- US2 テスト T016/T017/T018[P]。Polish: T021/T022/T024[P]。

## 実装戦略

1. **MVP first**: Phase 1→2→3（US1）で「校正器学習 + p vs p' 採用ゲート（joint 非悪化込み）」を最短達成。
2. **運用価値**: US2 で haircut + 校正適用 Kelly + raw/cal/cal+haircut 比較（Kelly リスク非悪化ガード）。
3. **013 整合**: US3 で 2×2(p×q) 二重補正検出。
4. 各 Checkpoint で独立テスト緑。憲法 II（校正器 walk-forward・選択窓内・p'/haircut 非還流・p≠q）/ III（NLL/Brier 主 + joint/Kelly 非悪化必須ガード）/ IV（p' は 009 入力一致・Σ=1）/ V（logic_version に校正情報、スキーマ変更なし）を全タスクで維持。
