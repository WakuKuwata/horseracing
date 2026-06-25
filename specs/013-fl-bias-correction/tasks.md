# Tasks: 人気-不人気バイアス補正（favorite-longshot bias correction）

**Input**: Design documents from `specs/013-fl-bias-correction/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/fl_calibrator.md, contracts/fl_evaluation.md, quickstart.md

**Tests**: 含む（憲法 II リーク + III 評価先行 + IV 整合性。校正・正規化後評価・walk-forward・リーク境界は必須）

**Organization**: User story 単位（P1 US1 学習/適用 → P1 US2 補正経路/配線 → P1 US3 評価 → P2 US4 CLI）。MVP=US1。

## パス規約

`probability`（校正器 + 補正経路 + 評価）を拡張、`betting`(011/012) は opt-in 配線。全パスはリポジトリルート相対。**新規依存なし**
（power=numpy 1次元 MLE）。isotonic/loglog は将来（正規化後目的の実装が非自明、`method` 引数は受けるが未実装は明示エラー）。

---

## Phase 1: Setup（共通基盤）

- [x] T001 [P] `probability/src/horseracing_probability/fl_bias.py` を新規作成し、値オブジェクト `FLCalibrator`(method/params/train_window/n_races/n_samples/odds_range/logic_version)・`CorrectedMarketProbs`(race_id/q/q_prime/field_size/excluded) の骨子を定義する（data-model.md §1–2、新規依存なし）
- [x] T002 [P] `probability/src/horseracing_probability/market_calibration.py` に評価レポート型 `QvsQpReport`・`DivergenceDeltaReport` を追加する（data-model.md §4–5）

**Checkpoint**: 型が存在し import 可能。

---

## Phase 2: Foundational（正規化後校正の核・全 US の前提）

**⚠️ CRITICAL: 校正は per-horse marginal ではなく「レース正規化後 q'」を対象にする。エンジン整合もここで確定。**

- [x] T003 `probability/src/horseracing_probability/fl_bias.py` に `apply_g(method, params, q) -> q_prime` を実装する: power=`q^γ` を各馬に適用し `q'_i=g(q_i)/Σ_j g(q_j)` に再正規化（Σ=1、単調保持）。**末尾に 009 `engine._normalize_clip`（renorm→clip[eps,1−eps]→renorm）と同一手順を適用**して**冪等**（`_normalize_clip(q')≈q'`）にし、極小テールは clip で端点へ寄せ再正規化。isotonic/loglog は将来（未実装は明示エラー）(research.md R1/R2 / data-model.md §2)
- [x] T004 `probability/src/horseracing_probability/fl_bias.py` に `fit_power_gamma(samples) -> tuple[float, int]` を実装する: 目的 `Σ_races −log(q_w^γ/Σ_j q_j^γ)` を **γ∈[GAMMA_MIN,GAMMA_MAX]（既定 [0.1,5.0]）の有界 1 次元最小化**（黄金分割等、決定論・seedless）。**情報レースのみ**（有効馬≥2 かつ q が全馬同一でない）を使用、同着・勝者なしは除外し件数記録、情報レース 0 件は **γ=1（恒等）+ 不十分マーク**を返す（research.md R1/R6）
- [x] T005 `probability/src/horseracing_probability/fl_bias.py` に walk-forward サンプルローダ `load_samples(session, *, date_from, date_to)` を実装する: `market_calibration._race_winodds_and_winner` を再利用し `(race_id, race_date, win_odds, winner|None)` を返す。strictly-before の境界は **`(race_date, race_id)` 辞書順**（race_date 常在・決定論、post_time が両側非 null なら intra-day 精緻化）。日付単位 `<=` 禁止、学習/評価窓は非重複(research.md R3)

### Foundational テスト

- [x] T006 [P] `probability/tests/unit/test_fl_apply.py` を作成: `apply_g` が q→q' 単調・レース内 Σ=1、power γ=1 で恒等（q'=q）、γ>1 で favorite 強化、**エンジン冪等性**（`engine._normalize_clip(q')≈q'`、**極小テール入力で clip 発火しても等価**）、決定論を検証（SC-001/SC-003）
- [x] T007 [P] `probability/tests/unit/test_fl_fit_power.py` を作成: 合成（favorite 過小評価の歪み q + 実現勝率）で `fit_power_gamma` が**正規化後**勝者尤度を最大化する γ を復元、**退化（全 q 同一/単走/情報 0 件→γ=1 フォールバック）**、γ が探索範囲内、同着/勝者なし除外、決定論を検証。**per-horse marginal ではなく正規化後を学習している**ことを回帰（SC-001/CRITICAL）

**Checkpoint**: 正規化後校正の核が動作・検証済み。US1/US2/US3 着手可能。

---

## Phase 3: User Story 1 - 校正器を学習し q→q' を適用 (Priority: P1) 🎯 MVP

**Goal**: 過去 (q, 実現勝敗) から walk-forward で FL 校正器を学習し、対象レースの q に適用して q'（Σ=1、単調）を得る。

**Independent Test**: 合成データで校正器学習 → 適用した q' が単調・Σ=1、学習に評価対象レースの結果を使わない（walk-forward）、
方式・γ・窓・サンプル数が再現メタに記録、決定論。

### 実装

- [x] T008 [US1] `probability/src/horseracing_probability/fl_bias.py` に `fit_fl_calibrator(samples, *, method="power", select="mle", min_samples=...) -> FLCalibrator` を実装する: **power のみ実装**（γ MLE、T004）、`method in {isotonic,loglog}` は **NotImplementedError**（将来）、方式/ハイパラ選択は**学習窓内**（最終評価未使用）、odds_range・n_races・n_samples 記録、logic_version 生成（方式/γ/窓/版）。p 非参照（FR-001/FR-004/research.md R3/R5）
- [x] T009 [US1] `probability/src/horseracing_probability/fl_bias.py` に `apply_calibrator(calibrator, win_odds) -> CorrectedMarketProbs` を実装する: q 算出（取消・除外・無効オッズ除外）→ `apply_g` → q'、field_size を**補正後の有効出走集合**から導出、学習レンジ外は端点クリップ + 範囲外件数を excluded/監査（FR-002/research.md R5）

### US1 テスト

- [x] T010 [P] [US1] `probability/tests/unit/test_fl_calibrator.py` を作成: power の fit→apply、単調・Σ=1、logic_version に方式/γ/窓/サンプル数、odds_range 外クリップ + 範囲外件数、**method=isotonic/loglog で NotImplementedError**、決定論、p 非参照を検証（SC-001/SC-006）
- [x] T011 [P] [US1] `probability/tests/integration/test_fl_walk_forward.py` を作成: 実 DB で `load_samples`+`fit_fl_calibrator` が**対象レース開始より厳密前**のレースのみ学習（評価対象レース結果の非使用、同日 race_id タイブレーク）、決定論を検証（SC-001/SC-002）

**Checkpoint**: US1 単独で動作・テスト緑。校正器を学習・適用できる（MVP）。

---

## Phase 4: User Story 2 - 補正済み q' で推定オッズ・EV を算出 (Priority: P1)

**Goal**: q' を 009/010 に通し補正済み推定オッズを得て、011/012 が opt-in で使う。生 q は後方互換。

**Independent Test**: 同一レースで生 q と補正 q' の推定オッズを比較し、補正ありが q' 由来・補正後単勝オッズは生オッズを厳密復元
しない・011/012 が opt-in で補正を使える・p 不変・q'/odds が win モデル特徴に入らない、を確認。

### 実装

- [x] T012 [US2] `probability/src/horseracing_probability/market_odds.py` の `estimate_market_odds` に `calibrator=None` を追加する: 指定時は q→q'(`apply_g` 経由、補正後 field_size)→009→`O_est=(1−控除率)/P_market(q')`、未指定は生 q（**後方互換**）。`is_estimated=True`（疑似）。p 非参照（FR-005/data-model.md §3）
- [x] T013 [US2] `betting/` の `exotic_ev.py`(candidate_bets) → `exotic_recommend.py` / `exotic_backtest.py` / `exotic_divergence.py` に `calibrator=None` を opt-in で通し、補正済み推定オッズで O_est/EV を算出できるようにする（無効時 011/012 と同一挙動、後方互換）。p（モデル側）は不変（FR-005/SC-003）

### US2 テスト

- [x] T014 [P] [US2] `probability/tests/unit/test_fl_estimate_odds.py` を作成: `estimate_market_odds(calibrator=...)` が q' 由来、calibrator=None で生 q（後方互換・既存挙動不変）、**補正後の推定単勝オッズが生オッズを厳密復元しない**（生 q では復元）ことを検証（SC-003/FR-006）
- [x] T015 [P] [US2] `betting/tests/unit/test_fl_leak_guard.py` を作成: **リーク・ガード** — odds/q/q' が `features`/`serving` の win モデル入力（特徴量）に一切渡らないことを assert（特徴量名集合に odds/q/q' が無い、p と q' が別物）。p≠q を回帰（SC-002/憲法 II）

**Checkpoint**: US2 単独で動作・テスト緑。補正済み推定オッズが opt-in で 011/012 に届く。

---

## Phase 5: User Story 3 - 補正の効果を評価（勝率校正 + 乖離） (Priority: P1)

**Goal**: 補正前後の勝率校正（採否ゲート）と推定 vs 実 exotic 乖離（補助診断）を計測。

**Independent Test**: walk-forward 評価期間で q vs q' の NLL/Brier/ECE（人気帯別、正規化後 q'）が算出され改善/悪化が定量化、
012 乖離が補正前後で並ぶ、全出力疑似評価・採否=勝率校正明示。

### 実装

- [x] T016 [US3] `probability/src/horseracing_probability/market_calibration.py` に `evaluate_q_vs_qprime(samples, calibrator, *, bands, bins=DEFAULT_BINS) -> list[QvsQpReport]` を実装する: 各レースで生 q と補正 q'（正規化後）を実現勝者に対し NLL/Brier/**ECE**、**信頼性曲線**（各ビン `(mean_pred, emp_rate, n)` を q/q' 両方、**固定既定ビン `DEFAULT_BINS`=10 等幅**・空ビン n=0 明示）、**人気帯別（固定境界・サンプル数併記）**、同着除外 + 件数、`improved`（採否=勝率校正）、`pseudo=True`（FR-007/research.md R6/R7）
- [x] T017 [US3] `probability/src/horseracing_probability/market_calibration.py` に `compare_divergence(session, *, date_from, date_to, calibrator, model_version=None) -> dict[str, DivergenceDeltaReport]` を実装する: 012 `betting.exotic_divergence` を生 q / 補正 q' で 2 回回し券種別 coverage_rate と log 比の **median/MAE/P90（生 q・補正 q' 両方）**を並べる。**診断のみ**（採否条件にしない）、`pseudo=True`（FR-008/research.md R7）

### US3 テスト

- [x] T018 [P] [US3] `probability/tests/unit/test_fl_evaluation.py` を作成: `evaluate_q_vs_qprime` が NLL/Brier/ECE を**正規化後 q'** で**固定既定ビン `DEFAULT_BINS`**算出（呼び出し側非依存で同値）、**信頼性曲線の各ビン (mean_pred,emp_rate,n)・空ビン n=0**、人気帯別 + サンプル数、同着除外、補正改善時 `improved=True`、不足データ fail-fast、決定論を検証（SC-004/SC-007）
- [x] T019 [P] [US3] `probability/tests/integration/test_fl_eval_db.py` を作成: 実 DB(walk-forward、評価期間が学習窓と非重複)で q vs q' 校正が算出され、評価期間重複時 ERROR（リーク防止）、決定論を検証（SC-004/SC-002）

**Checkpoint**: US3 単独で動作・テスト緑。採否を勝率校正で判断できる。

---

## Phase 6: User Story 4 - CLI で学習・評価 (Priority: P2)

**Goal**: CLI で校正器学習・評価（walk-forward）、適用切替。

**Independent Test**: CLI で fl-fit / fl-evaluate を実行し、校正器要約と校正/乖離指標が方式・学習窓・疑似評価・採否=勝率校正明示で表示。

### 実装

- [x] T020 [US4] `probability/src/horseracing_probability/cli.py` に `fl-fit`（`--train-from --train-to --method`）を追加する: `load_samples`+`fit_fl_calibrator` を呼び、方式・γ・学習窓・サンプル数・q 範囲を表示（FR-011）
- [x] T021 [US4] `probability/src/horseracing_probability/cli.py` に `fl-evaluate`（`--train-from --train-to --eval-from --eval-to --method`）を追加する: 学習→評価で q vs q' の NLL/Brier/ECE（人気帯別）+ 乖離前後比較を**疑似評価・採否=勝率校正**明示で表示。評価期間が学習窓と重複なら ERROR（FR-011/リーク防止）

### US4 テスト

- [x] T022 [P] [US4] `probability/tests/integration/test_fl_cli.py` を作成: `fl-fit`/`fl-evaluate` が実 DB で実行され、校正器要約・校正指標（人気帯別）・採否=勝率校正明示が出力に含まれ、学習/評価窓重複で ERROR を検証（FR-011）

**Checkpoint**: 全 US 完了。CLI から学習・評価が操作可能。

---

## Phase 7: Polish & Cross-Cutting

- [x] T023 [P] `probability/src/horseracing_probability/__init__.py` に公開 API（fit_fl_calibrator / apply_calibrator / evaluate_q_vs_qprime）を export する
- [x] T024 [P] lint 解消: `cd probability && uv run ruff check .`、`cd betting && uv run ruff check .`
- [x] T025 全テスト緑を確認: `cd probability && uv run pytest tests/unit && uv run pytest -m integration`、`cd betting && uv run pytest`
- [x] T026 [P] [quickstart 検証] `specs/013-fl-bias-correction/quickstart.md` を実 DB(2007 学習 / 2008 評価)で実行: fl-fit → fl-evaluate で校正改善・乖離前後・リーク境界・疑似評価明示を確認（SC-001〜SC-007）

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001/T002 [P]。
- **Phase 2 (Foundational)**: Setup 後。T003→T004→T005（同一 fl_bias.py、順次）、テスト T006/T007 [P]。**全 US をブロック**（正規化後校正の核）。
- **Phase 3 (US1, P1, MVP)**: Foundational 後。T008→T009、テスト T010/T011 [P]。
- **Phase 4 (US2, P1)**: Foundational + US1（apply_calibrator）後。T012→T013、テスト T014/T015 [P]。
- **Phase 5 (US3, P1)**: Foundational + US1 + US2（estimate_market_odds(calibrator) / exotic_divergence）後。T016→T017、テスト T018/T019 [P]。
- **Phase 6 (US4, P2)**: US1+US3 後。T020→T021、テスト T022 [P]。
- **Phase 7 (Polish)**: 全実装後。

### User Story 独立性

- US1（学習/適用）は Foundational のみに依存し単独完結（MVP）。US2 は US1 の apply、US3 は US2 の補正経路 + 012 乖離に依存。US4 は CLI で束ねる。

## Parallel 実行例

- Setup: T001/T002 並走。Foundational テスト T006/T007 並走。
- US1: T010/T011 並走。US2: T014/T015 並走。US3: T018/T019 並走。Polish: T023/T024/T026 並走。

## 実装戦略

1. **MVP first**: Phase 1→2→3（US1）で「正規化後 FL 校正器の学習・適用」を最短達成。
2. **補正経路**: Phase 4（US2）で 010/011/012 に opt-in 接続（生 q 後方互換）。
3. **評価先行（憲法 III）**: Phase 5（US3）で勝率校正（採否ゲート）+ 乖離（診断）。
4. **運用性**: Phase 6（US4）CLI、Phase 7 lint/全テスト/quickstart 実 DB。
5. 各 Checkpoint で独立テストを緑に。憲法 II（リーク: 正規化後学習・walk-forward 厳密前・q' 非特徴量）/ III（採否=勝率校正）/ IV（Σ=1）を全タスクで維持。
