# Tasks: モデル改善 — リーク安全な特徴量拡張と walk-forward 採用ゲート

**Input**: Design documents from `specs/020-model-features/`
**Prerequisites**: plan.md, spec.md, research.md (R1–R7), data-model.md, contracts/feature_eval.md, quickstart.md

**Tests**: 含む（憲法 II リーク / III 評価先行 / IV 整合 / V 監査は必須。pytest + testcontainers + 合成データ）

**Organization**: User story 単位（P1 US1 新規リーク安全特徴量 → P1 US2 fold 内選択 採用ゲート → P2 US3 下流 diagnostic）。MVP=US1。

## パス規約

既存 `features/` `training/` `eval/` を拡張（新パッケージなし）。**スキーマ変更なし**（feature_version=features-005、
head 0006）。確認済み: `registry.FeatureMeta`=feature spec table、`history._cumulative_before`=daily cumsum−当日
（厳密前+同日除外）、`merge_asof(backward, allow_exact_matches=False)`=同日除外 as-of。新特徴はこの実証済み機構を
転用。確率は win→joint(009) 維持、market odds/結果は特徴にしない。

---

## Phase 1: Setup（registry group・版・テスト補助）

- [X] T001 `features/src/horseracing_features/registry.py` に `FEATURE_GROUPS: dict[str, str]`（特徴名→group: recent_form/aptitude/race_condition/human_form）と group 取得ヘルパを追加。feature_version 定数を features-005 に bump（R1）
- [ ] T002 [P] `features/tests/_leakcheck.py` を作成: cutoff 検証ヘルパ（対象レース当日以降のデータを変更しても特徴量が不変）と target-row 除外ヘルパ（対象行/同日結果を変更しても跨馬統計が不変）の共通アサーション（R2, FR-003）

**Checkpoint**: group/版/リーク検証補助が揃う。

---

## Phase 2: Foundational（builder 結線 — 新特徴の共通土台）

- [X] T003 `features/src/horseracing_features/builder.py` に新特徴フレームを matrix へ結線する seam を追加（`assemble_feature_matrix`/`build_feature_matrix` が新特徴を含め、`validate_columns` が全列を許可）。feature_version を features-005 として出力（R1, FR-013）

**Checkpoint**: 新特徴を matrix に載せる結線が確定（US1 で各特徴を実装）。

---

## Phase 3: User Story 1 - リーク安全な新規特徴量（Priority: P1）🎯 MVP

**Goal**: 9 新特徴を as-of/out-of-fold/同日除外で実装し、cutoff/target-row 除外/Unknown をテストで保証。

**Independent Test**: 各特徴に feature spec（registry 登録）+ cutoff テスト、跨馬に target-row 除外テスト、新馬で 0 代入なし。

### 実装

- [X] T004 [US1] `features/src/horseracing_features/history.py` に **recent_form**（avg_last3_finish・recent_win_rate）+ **aptitude**（dist_band_win_rate・dist_band_avg_finish・surface_win_rate）+ **class_transition** を as-of で追加（`_cumulative_before` の daily cumsum−当日 / `merge_asof(backward, exact 無し)` を転用、馬単位・同日除外）。registry に FeatureMeta(NULL) + group 登録（R2/R3, FR-001/002）
- [X] T005 [US1] `features/src/horseracing_features/human_form.py` を新規作成: **jockey_win_rate**・**trainer_win_rate** を jockey_id/trainer_id でグルーピングし daily cumsum−当日（=対象行+同日除外）+ walk-forward 前のみで as-of 集計。registry に FeatureMeta(NULL)+group=human_form 登録。builder に結線（R2, FR-003）
- [X] T006 [US1] `features/src/horseracing_features/builder.py`（or registry）に **field_size**（当該レース出走頭数、結果非依存、ZERO_OK）を追加・登録（race_condition group）（R3, FR-001）

### US1 テスト

- [X] T007 [P] [US1] `features/tests/unit/test_new_features_cutoff.py` を作成（_leakcheck 使用）: 全新特徴で対象レース当日以降のデータ変更が特徴量を変えない（cutoff、SC-001）、新馬/過去不在で Unknown（0 代入なし、SC-003）を検証
- [X] T008 [P] [US1] `features/tests/unit/test_human_form_leak.py` を作成: jockey/trainer フォームが対象行の着順・同日他レース結果の変更で不変（target-row + 同日除外、SC-002）、out-of-fold（対象レースより前のみ）を検証

**Checkpoint**: US1 単独で新特徴がリーク安全に算出・検証済み（MVP）。

---

## Phase 4: User Story 2 - fold 内選択の採用ゲート（Priority: P1）

**Goal**: 新特徴モデルを fold 内選択の walk-forward で baseline と比較、LogLoss 改善かつ ECE 非悪化+fold 別差分で採用判定。

**Independent Test**: fold 内 inner train/val で選択・tuning 完結、OOS で new vs baseline の指標+fold 別差分、group ablation、選択リーク無し、決定論。

### 実装

- [X] T009 [US2] `training/src/horseracing_training/` に **fold 内 inner train/val のハイパラ選択・early stopping フック**（**特徴選択は行わない＝候補集合は事前固定**、OOS を見て特徴を選ばない）と正則化レンジ事前固定（min_data_in_leaf/lambda/feature_fraction/num_leaves）+ fold 安定性（gain/SHAP 符号・順位）算出を追加（R4/R5, FR-005/009、analyze F1）
- [X] T010 [US2] `eval/src/horseracing_eval/feature_eval.py` を新規作成: 候補特徴を事前固定し、walk-forward 各 fold で「**固定候補集合**（fold 内ハイパラのみ）vs 現行 baseline」を OOS 比較し `AdoptionReport`（fold 別+平均 LogLoss/Brier/AUC/ECE、勝ち fold 数・最悪 fold・ECE 差分、primary_pass=LogLoss 改善 かつ ECE 非悪化、adopted）を返す。採用時は同一固定集合を全体再学習（評価＝デプロイ一致）（R4/R5, FR-005/006/007, data-model §3/§5）
- [X] T011 [US2] `eval/src/horseracing_eval/ablation.py` を新規作成: group（recent_form/aptitude/race_condition/human_form）単位 ablation で各 group の寄与（LogLoss 差）を分離算出（R3/R5, FR-008）
- [X] T012 [US2] `eval/src/horseracing_eval/cli.py` に `feature-eval --from --to [--seed]` と `feature-ablation --from --to --groups` を追加（contracts/feature_eval.md, FR-014）

### US2 テスト

- [X] T013 [P] [US2] `eval/tests/integration/test_feature_adoption.py` を作成（合成データ）: 候補特徴が事前固定で OOS を見て特徴選択しない（fold 内はハイパラのみ、選択リーク無し、SC-004）、fold 別+平均指標算出、primary=LogLoss 改善 かつ ECE 非悪化、fold 別差分（勝ち fold/最悪 fold/ECE 差）で偶然 fold を排除、baseline 未超過なら adopted=false（false positive なし、SC-010）、同一 seed で再現（SC-004/005/006/009）
- [X] T014 [P] [US2] `eval/tests/integration/test_ablation.py` を作成: group 単位の寄与が分離報告され、human_form と recent_form の寄与が判別できる（SC-007）。**group ablation は diagnostic で採用特徴の選別に使われない**（候補は事前固定）ことを確認（SC-004/SC-007）

**Checkpoint**: US2 単独で正当な採用判定が可能。

---

## Phase 5: User Story 3 - 下流 diagnostic と市場超過の現実評価（Priority: P2）

**Goal**: pseudo-ROI/Kelly（SECONDARY）+ 市場 q edge を diagnostic 算出、主採用ゲートにしない。

**Independent Test**: 採用候補で pseudo-ROI/Kelly + p−q calibration/edge bucket/q 条件付き LogLoss を算出、主判断は win 品質。

### 実装

- [X] T015 [US3] `eval/src/horseracing_eval/market_edge.py` を新規作成: 市場 q（010）に対する p−q calibration・edge bucket 別実現勝率・q 条件付き LogLoss を算出（diagnostic）。011/016 の pseudo-ROI/Kelly backtest を SECONDARY として呼ぶラッパ + cli `feature-diagnostic`（R6, FR-010/011）

### US3 テスト

- [X] T016 [P] [US3] `eval/tests/integration/test_market_edge.py` を作成（合成データ）: p−q calibration・edge bucket・q 条件付き LogLoss が算出され、pseudo-ROI/Kelly が SECONDARY（主採用ゲートにしない）であること、「絶対校正改善≠市場超過」明示を検証（SC-008）

**Checkpoint**: 全 P1+P2 完了。win 改善の下流波及を現実的に把握。

---

## Phase 6: Polish & Cross-Cutting

- [X] T017 [P] `features/tests/unit/test_leak_guard.py`（or 既存拡張）: market odds・race_results 由来の値が model_input_features に出現しないこと（憲法 II）を assert。さらに **020 が migration を追加せず新 ORM テーブルを定義しないこと**（db/migrations head=0006 不変、features/eval/training に `__tablename__` 追加なし）を静的検証（F2, FR-013/SC-009）
- [X] T018 `specs/020-model-features/quickstart.md` を実行（実 DB スモーク）: `feature-eval` + `feature-ablation` + `feature-diagnostic` を実データで走らせ採用判定/寄与/diagnostic を確認（[[local-db-setup]]、改善が無ければ adopted=false を確認）
- [X] T019 [P] `features/`/`eval/` の lint/test を通す（`uv run ruff check` / `uv run pytest`）
- [X] T020 [P] `CLAUDE.md` に 020 の 1 行サマリを追記（011–019 と同形式: 新特徴4 group・既存 as-of/同日除外機構転用・跨馬は対象行+同日除外・fold 内選択・LogLoss+ECE+fold 別差ゲート・group ablation・成功=OOS win 改善・スキーマ変更なしを要約）

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001→T002[P]。
- **Phase 2 (Foundational)**: Setup 後。T003。**US1 をブロック**（builder 結線）。
- **Phase 3 (US1, MVP)**: Foundational 後。T004→T005→T006、テスト T007/T008[P]。
- **Phase 4 (US2)**: US1（新特徴）後。T009→T010→T011→T012、テスト T013/T014[P]。
- **Phase 5 (US3)**: US2（評価ハーネス）後。T015、テスト T016[P]。
- **Phase 6 (Polish)**: 全実装後。T017/T019/T020[P]、T018。

### User Story 独立性

- US1 は新特徴のリーク安全実装で独立（MVP）。US2 は採用ハーネス（US1 の特徴を評価）。US3 は diagnostic（US2 の候補モデルに適用）。

## Parallel 実行例

- Setup: T002[P]。US1 test T007/T008[P]。US2 test T013/T014[P]。Polish: T017/T019/T020[P]。

## 実装戦略

1. **MVP first**: Phase 1→2→3（US1）で「リーク安全な新特徴 + cutoff/target-row テスト」を確立。
2. **採用判定**: US2 で fold 内選択 walk-forward + LogLoss/ECE ゲート + group ablation。
3. **現実評価**: US3 で下流 diagnostic（市場超過は努力目標）。
4. 各 Checkpoint で独立テスト緑。憲法 II（as-of/out-of-fold/同日除外・選択 fold 内・odds/結果非特徴）/ III（OOS ゲート・fold 別差・baseline 超えのみ採用）/ IV（win→joint 維持・Unknown）/ V（feature_version・決定論）/ VI（スキーマ変更なし）を維持。
