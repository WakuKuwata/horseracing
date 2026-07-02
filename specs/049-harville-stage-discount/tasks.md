# Tasks: Harville stage 割引 — top2/top3(連対・複勝)確率の校正改善

**Input**: Design documents from `specs/049-harville-stage-discount/`

**Prerequisites**: plan.md, spec.md, research.md (D1–D8), data-model.md, contracts/stage-discount.md, quickstart.md

**Tests**: 含む(憲法の品質ゲート: leakage test・確率整合性 test・評価ハーネス test は必須)。契約 INV-S1〜S9 を直接テスト化する。

**Organization**: US1=割引導出+λフィット(P1)、US2=事前登録ゲート評価(P2)、US3=採用時の製品結線(P3)。**US3 は T019 のゲート判定が ADOPTED の場合のみ実施**。

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

**Purpose**: バイト一致検証の基準を固定する(コード変更前のグリーン確認)

- [x] T001 変更前ベースライン確認: `eval`/`probability`/`training`/`betting`/`serving` の既存スイートを実行し全緑を記録(quickstart §5 のループ)。以降の INV-S1/S9 バイト一致検証の基準点とする

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 全 US が依存する共有プリミティブ

- [x] T002 `eval/src/horseracing_eval/stage_discount.py` 新規作成: `StageDiscount` dataclass(lambda2/lambda3/n_races_l2/n_races_l3/fallback、data-model.md 準拠)+ 決定論 golden-section(範囲 [0.1,5.0]・tol=1e-6、fl_bias._golden_min 同型である旨と依存方向の理由 [research D2] を docstring に明記)。contracts/stage-discount.md を正とする

**Checkpoint**: 共有プリミティブ完成 — US1 着手可能

---

## Phase 3: User Story 1 - 割引導出と λ フィット (Priority: P1) 🎯 MVP

**Goal**: λ 指定の stage 割引付き top2/top3・joint 導出(opt-in、既定=現行バイト一致)と、(win ベクトル, 確定 1〜3 着) からの決定論 λ フィット

**Independent Test**: λ=1 で既存とバイト一致 / λ<1 で方向性(INV-S7)/ フィット決定論・fallback を単体テストで検証(DB 不要)

### Implementation for User Story 1

- [x] T003 [US1] `eval/src/horseracing_eval/stage_discount.py` に `discounted_topk(win, sd) -> (top2, top3)` 実装(contract の top2/top3 式、O(n³)、残存質量 eps ガードは既存 `harville_topk` の `_EPS` 規律と同一)
- [x] T004 [US1] `eval/src/horseracing_eval/baselines.py` の `harville_topk` に `lambda2=1.0, lambda3=1.0` キーワード引数を追加。**`lambda2 == 1.0 and lambda3 == 1.0` は既存ループへの明示分岐(INV-S1 バイト一致保証)**、それ以外は `discounted_topk` へ委譲
- [x] T005 [US1] `eval/src/horseracing_eval/stage_discount.py` に `fit_stage_discount(samples, *, min_races=300) -> StageDiscount` 実装: λ_2 =「1・2 着一意」レースの条件付き NLL、λ_3 =「1〜3 着一意」レースの条件付き NLL を独立に golden 最小化(contract 式)。同着等の除外件数を返す。min_races 未満または境界張り付き → identity fallback(fallback=True)
- [x] T006 [US1] `probability/src/horseracing_probability/engine.py` の `joint_probabilities` に `stage_discount=None` 引数を追加: exacta/trifecta の逐次分母を w2/w3(contract 式)に、`_place` も同一 λ の `harville_topk` 呼び出しに。**None は既存コードパス(INV-S9 バイト一致)**
- [x] T007 [US1] `probability/src/horseracing_probability/consistency.py` を λ 対応に拡張: joint marginal == **同一 λ の** harville_topk を検証(INV-S5)。λ 未指定の既存検証はバイト不変
- [x] T008 [US1] `training/src/horseracing_training/predictor.py` の `assemble_predictions` に `stage_discount=None` を透過(win 正規化は不変、tail 導出のみ λ 付き harville_topk へ)。既定 None は現行バイト一致

### Tests for User Story 1

- [x] T009 [P] [US1] `eval/tests/test_stage_discount.py`: INV-S1(λ=1 で既存 harville_topk とバイト一致)・INV-S3/S4(単調・Σtop2≈2・Σtop3≈3、λ∈{0.3,0.7,1.5,5.0} で)・INV-S7(方向性)・INV-S6(フィット決定論)・同着除外・min_races/境界 fallback
- [x] T010 [P] [US1] `probability/tests/test_stage_discount_engine.py`: 割引時の Σexacta≈1・Σtrifecta≈1・quinella/trio/wide=順序和・joint marginal==discounted_topk(INV-S5)・place の field_size ルール不変・**stage_discount=None で全出力が既存とバイト一致(INV-S9)**
- [x] T011 [US1] `training/tests/test_predictor.py` 拡張: `assemble_predictions` の sd=None バイト一致 + sd 指定時に win 不変・top2/top3 のみ変化(INV-S2)

**Checkpoint**: 導出+フィットが単体で完全動作(DB 不要)。MVP。

---

## Phase 4: User Story 2 - 事前登録ゲートでの採否判定 (Priority: P2)

**Goal**: 18-fold A/B(単一学習パス)+ exotic 非悪化比較を実 DB で機械実行し、事前登録ゲートで採否を確定する

**Independent Test**: 合成 predictor での A/B ロジック検証(win 一致・fold2008=identity・ゲート判定)+ 実 DB 実行でレポート出力

### Implementation for User Story 2

- [ ] T012 [US2] `probability/src/horseracing_probability/model_calibration.py` に `load_topk_samples(session, *, date_from, date_to)` 追加: prediction_runs×race_predictions×race_results から (race_id, race_date, win ベクトル, 1〜3 着 horse_id) を返す(started のみ・engine 正規化・result_status='finished' のみ)。**run 選択は `load_p_samples` と同一規則(latest run、`_latest_run_predictions` 流用)**(analyze U2、監査一貫性)。**既存 `load_p_samples` は不変**
- [ ] T013 [US2] `eval/src/horseracing_eval/stage_discount_eval.py` 新規: `evaluate_stage_discount(session, predictor, ...)` — 既存 `expanding_folds` 流用、fold ごとに predictor fit→valid 予測の win ベクトル収集、λ̂=**前 fold pooled OOS 予測からフィット**(research D3、fold 2008=identity)、baseline(λ=1) vs candidate の top2/top3 LogLoss/ECE/reliability を fold 別+overall 採点、**win 指標の一致検証(diff==0)**、事前登録ゲート判定(PRIMARY: top2/top3 LogLoss・ECE 改善+strict majority / ガード: worst-fold top3 dLogLoss ≤ +5e-3)を含むレポート dataclass を返す(predictor-agnostic、training 非依存)
- [ ] T014 [US2] `training/src/horseracing_training/cli.py` に `stage-discount-eval` サブコマンド追加(LightGBMPredictor 注入、feature-eval 同型、レポート整形出力)
- [ ] T015 [US2] `betting/src/horseracing_betting/stage_discount_compare.py` 新規: 同一レース集合・同一 011/016 条件・同一オッズ・**製品構成の betting 経路(two_gamma 込み)**で λ=1 vs λ̂ の**複勝・ワイド・三連複 pseudo-ROI 比較**(research D7)。λ̂ は分布一致原則(D4)に従い **two_gamma 適用後の win ベクトルでフィット**(load_topk_samples → pcal 適用 → fit_stage_discount、校正器・λ とも厳密前)。実行前サンプル密度サマリを必ず出力(048 教訓)。MUST 判定: 各差 ≥ −0.005(spec US2 に事前登録済み)
- [ ] T016 [US2] `betting/src/horseracing_betting/cli.py` に `stage-discount-backtest-compare` サブコマンド追加
- [ ] T017 [P] [US2] `eval/tests/test_stage_discount_eval.py`: 合成 predictor で A/B — win 指標が全 fold で完全一致・先行 fold なし=identity・ゲート判定ロジック(改善/悪化ケース)・決定論
- [ ] T018 [P] [US2] leak-guard(INV-S8): `eval/tests/test_stage_discount.py` に「フィット境界=対象レース(cutoff)でサンプル 0 → identity」テスト、`probability/tests/test_load_topk_samples.py` に厳密前(race_before、同日除外)・race_id タイブレーク・同着除外テスト。λ/割引値が特徴に還流しない検査は既存 leak-guard パターン(disallowed token)に `sdisc` を追加
- [ ] T019 [US2] **実 DB でゲート実行(採否決定点)**: quickstart §2〜3 のとおり `stage-discount-eval` と `stage-discount-backtest-compare` を実行し、fold 別数値・reliability(top3 高帯の乖離縮小)・exotic 差分・採否判定を `specs/049-harville-stage-discount/spec.md` の Status と結果セクションに記録。**不採用なら Phase 5 をスキップし負結果を記録して Phase 6 へ**

**Checkpoint**: 採否が機械確定。ADOPTED → Phase 5 / 不採用 → Phase 6

---

## Phase 5: User Story 3 - 採用時の製品結線 (Priority: P3) ⚠️ T019 ADOPTED 時のみ

**Goal**: serving の top2/top3 永続化と betting 推奨経路が walk-forward フィット済み λ で割引され、logic_version に監査記録される

**Independent Test**: 実 DB E2E(quickstart §4)— win バイト不変・top2/top3 変化・lv 記録・API 透過

### Implementation for User Story 3

- [ ] T020 [US3] `serving/pyproject.toml` に `horseracing-probability` 依存を追加(analyze U1、非循環)した上で、`serving/src/horseracing_serving/pipeline.py`: `_predict_persist` 前に product λ フィット(**素の永続化 p でフィット=素の p に適用、分布一致 D4**。serve=レース前、backfill=日単位 1 回 — 046 `_fit_product_p_calibrator` と同型の境界・走査 bound)を追加し `assemble_predictions(stage_discount=sd)` へ結線。logic_version に `sdisc=harville;l2=...;l3=...;n2=...;n3=...`(identity 時 `sdisc=identity`)を追記(data-model.md 形式)
- [ ] T021 [US3] `betting/src/horseracing_betting/` 推奨経路(`_generate_product_set` 系)で `joint_probabilities(stage_discount=sd)` を opt-in 結線(046 の pcal 結線と同型・win 側 Kelly は影響なしを確認)。**λ̂ は T015 と同じく two_gamma 適用後の p' でフィットしたもの**(分布一致 D4 — serving の λ̂ と別フィット)。lv 追記は同一形式
- [ ] T022 [P] [US3] テスト: `serving/tests` に win_prob バイト不変+top2/top3 変化+lv 記録+identity fallback、`betting/tests` に sd=None 経路の lv バイト不変(後方互換)+sd 指定時の複勝系 P_model 変化
- [ ] T023 [US3] 実 DB E2E(quickstart §4): serve 実行 → race_predictions/recommendations/lv 確認 → API 透過(openapi drift-check 一致)を検証し結果を spec に記録

**Checkpoint**: 製品で校正済み連対率・複勝率が提供される

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T024 全パッケージ回帰(quickstart §5)+ `front` の drift-check(openapi 不変確認)
- [ ] T025 [P] `specs/049-harville-stage-discount/spec.md` の Status 更新(ADOPTED/負結果+実測数値)と CLAUDE.md SPECKIT ブロックの要約更新(agent-context)
- [ ] T026 [P] メモリ更新: `~/.claude/.../memory/` に feature-049 結果ノート(採否・λ̂・top3 ECE 変化・「導出層は win レバー棚卸しの外」の学び)+ MEMORY.md 索引行

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 → Phase 2 → Phase 3(US1)→ Phase 4(US2)→ [T019 ゲート] → Phase 5(US3、ADOPTED 時のみ)→ Phase 6**
- US2 は US1 の導出+フィットに依存(独立実装不可 — 評価対象が US1 の成果物)
- US3 は T019 の ADOPTED 判定に条件依存(不採用なら実施しない、opt-in 実装はマージ可)

### Within Each User Story

- US1: T003→T004(委譲先が先)、T005 は T002 のみ依存で T003 と並行可、T006/T007 は T003 後、T008 は T004 後。テスト T009–T011 は対応実装後(T009 と T010 は並行可)
- US2: T012 と T013 は並行可(別パッケージ)、T014 は T013 後、T015 は T012+US1 後、T016 は T015 後、T019 は全 US2 実装+テスト後
- US3: T020 と T021 は同一フィット関数を共有するため T020 先行、T022 は並行可、T023 は最後

### Parallel Opportunities

- T009 ∥ T010(eval/probability 別パッケージのテスト)
- T012 ∥ T013、T017 ∥ T018
- T022 ∥(T023 準備)、T025 ∥ T026

---

## Implementation Strategy

**MVP = Phase 3(US1)完了時点**: DB 不要で「λ=1 バイト一致+割引の数学的正しさ」が単体テストで証明された状態。ここで一度止めて検証可能。

**採否分岐が本 feature の中心**: Phase 4 の T019 が decision point。事前登録ゲート(spec US2)を実行前に変更しないこと(憲法 III)。不採用でも US1/US2 の opt-in 実装・評価インフラはマージ価値がある(負結果の記録も成果)。

**注意(実装時)**:
- すべての新引数の既定値は「現行挙動とバイト一致」(INV-S9)。既存テストが 1 つでも赤くなったら後方互換違反を疑う
- 048 教訓: T015/T019 の実行前にサンプル密度を確認し、窓の決定は結果を見る前に行う
- codex CLI が復旧していれば T019 の前に second opinion を再試行(plan の deviation 記録参照)
