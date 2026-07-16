---
description: "Task list for feature 073 — Evaluation Contract v2 & Historical Freeze"
---

# Tasks: Evaluation Contract v2 & Historical Freeze

**Input**: Design documents from `/specs/073-eval-contract-correctness/`

**Prerequisites**: plan.md, spec.md, research.md (D1–D8), data-model.md, contracts/cli.md, quickstart.md

**Tests**: 含める(憲法 II/III/V が leak-guard・parity・決定論・評価ハーネス test を必須化)。

**Organization**: user story 単位。US1/US2=P1、US3=P2、US4=P3。

**制約**: スキーマ変更ゼロ・migration なし・再学習/昇格/active 書換なし・既存 active serving 予測バイト不変。触るのは `eval/` と `training/` のみ(`probability/`=074、`api/`=075 には触れない)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 別ファイル・依存なしで並列可
- **[Story]**: US1–US4(setup/foundational/polish は無印)

---

## Phase 1: Setup

**Purpose**: 着手ブロッカーの解消と事前登録

- [X] T001 現 active の `model_version` を実 DB(horseracing)で確定 → **`lgbm-063`(features-017、lgbm-062 は retired)**。model/calibrator/preprocessor digest を quickstart §0 に記録(062/063 byte 一致確認済み)。parity oracle 固定・ブロッカー解消(D8)。
- [X] T002 [P] `specs/073-eval-contract-correctness/gate-config.json` を新規作成し v2 事前登録値を固定(`evaluation_contract_version="v2"`・`eval_window`・`no_decision_min_days=10`・`ece_subsets`[確率/odds/p/q/tail の境界・欠損bucket・最低件数・最低開催日数]・`tail_mask`[共通 or active_result_blind]・`bootstrap.primary="race_day_cluster_bootstrap_ci_v1"`・`bootstrap.sensitivity=[2d,3d,4d,week,meeting]`・決定論許容誤差`<1e-9`・`num_threads=1`)。OOS 結果を見る前に固定(III、contracts/cli.md)。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: US1/US3 が共有する監査 artifact 骨格と gate-config ローダ。**⚠️ 完了まで US1/US3 の実装を始めない**。

- [~] T003 監査 artifact の骨格を追加(部分完了): `PairedReport` に `decision`/`decision_reason`/`evaluation_contract_version`/`gate_config_hash` を追加、既存 `candidate/active_recipe_hash`・`race_id_set_hash` を活用。**残**: `source_hash`/`result_hash`・`*_artifact_checksum`・`started_all_set`・`determinism_proof` の付与(T014 で完結)。
- [X] T004 `eval/src/horseracing_eval/decision.py` に `gate_config_hash`(`_comment` 無視の canonical hash)と `assert_confirmatory`(未知/欠落 config・`evaluation_contract_version` 不一致・hash 不一致・eval_window 不一致 → 型付きエラー)を実装。**CLI への結線は T015**。

**Checkpoint**: 監査骨格と fail-closed ローダが揃い、US1/US3 着手可能。

---

## Phase 3: User Story 1 - 採用判定を単一三値・決定論・監査つきに (Priority: P1) 🎯 MVP

**Goal**: 採用可否が operator 手作業 0 で単一 enum(ADOPT/REJECT/NO_DECISION)として得られ、決定論で再現し、監査 artifact に契約 version と hash 群が残る。

**Independent Test**: 既存 candidate×active ペアで paired-eval を実行 → 単一 enum、不足入力で NO_DECISION、2 回実行で指標差 <1e-9、監査 JSON に全 hash。

### Tests for User Story 1

- [X] T005 [P] [US1] `eval/tests/unit/test_gate_decision.py` に三値真理値表テスト(ADOPT/REJECT/NO_DECISION の全分岐)。**PASS**。
- [X] T006 [P] [US1] 同上ファイルに境界テスト(開催日 9 vs 10・空 window(n_days=None)・underpowered/MISSING critical subgroup が黙って PASS しない)+ confirmatory fail-closed(config 欠落/hash 不一致/window 不一致)。**PASS**。
- [X] T007 [P] [US1] `eval/tests/unit/test_paired.py::test_paired_eval_is_deterministic_same_seed` に同一 seed で paired-eval を 2 回実行し winner NLL・paired 差・CI・decision・`to_dict()` 全体が <1e-9/完全一致を assert(SC-003)。DB-free fake factory で eval 契約の決定論を検証(LightGBM 単一 thread 決定論は training の既存 test_determinism が担保)。**PASS**。
- [X] T008 [P] [US1] `eval/tests/unit/test_started_all_harness.py` に harness 本体の started-all(DNF=win0)採点を assert(default off=byte 同一・on で全 starter 採点)。**PASS**。
- [X] T009 [P] [US1] leak-guard: eval→training import 境界は既存 `test_leak_guard_068.test_no_eval_file_imports_training` が新 `decision.py` も含め全 eval ファイルを網羅。加えて `test_gate_decision.py` に `final_decision` の入力非変更(純粋性)を assert。**PASS**。

### Implementation for User Story 1

- [X] T010 [US1] tri-value `decision` を **新規 `eval/src/horseracing_eval/decision.py`**(`final_decision` 純関数 + enum)で実装し `paired_eval` に結線、`PairedReport.decision`/`decision_reason` を追加。**`GateResult` は不変**(068/069 後方互換=旧 `adopted` bool 温存、122 eval unit 緑で実証)。旧 boolean は `decision=="ADOPT"` と後方一致。research D2。
- [X] T011 [US1] `final_decision` が `eval_window.min_eval_days`/`subgroup_guard.no_decision_min_days` を実判定へ結線(n_days 不足→NO_DECISION・critical subgroup FAIL→REJECT・underpowered→NO_DECISION)。`assert_confirmatory` を T004 で実装(**CLI 結線は T015**)。
- [X] T012 [US1] `harness.py` に `_score_race_started_all`(population_masks 由来・DNF=win0)を追加し `evaluate(started_all=True)` opt-in で `EvalResult.started_all_win`(+n_started)を出力。**default off=既存 summary byte 同一**(021/harness テスト無傷)。research D3。
- [ ] T013 [US1] `paired.py` に `ece_by_subset`(全体+確率/odds/p/q 帯+共通 tail mask)を実装。gate-config に `ece_subsets`/`tail_mask` は事前登録済み。**未着手**(現状 `ece_equal_mass`+`ece_by_band` のみ)。research D5。
- [~] T014 [US1] 監査 artifact 出力: `evaluation_contract_version`/`gate_config_hash` は付与済み。**残**: `source_hash`/`result_hash`/`*_artifact_checksum`/`started_all_set`/`determinism_proof`。
- [X] T015 [US1] `training/src/horseracing_training/cli.py` の `paired-eval` に単一 `DECISION=` 出力(cause/contract/gate_hash)・`--confirmatory`+`--gate-config-hash`(`assert_confirmatory` 結線=eval 前に fail-closed)・`--compute-sensitivity`(v2 感度)を追加。read-only 維持・CLI import smoke 緑。contracts/cli.md。

**Checkpoint**: US1 単独で「正しい物差し」が機械判定として成立。

---

## Phase 4: User Story 2 - split を recipe 明示化し既存 active を凍結 (Priority: P1)

**Goal**: calibration split が recipe の明示フィールドになり、既存 active が `race_count_v1` で digest 凍結され、split を変えれば recipe_hash と model_version が必ず変わり、serving 予測はバイト不変。

**Independent Test**: `race_count_v1` の recipe_hash が既存値と一致、`race_day_v1` で hash 変化、同一 model_version の split 変更再学習が拒否、active の予測 16 頭 mismatch 0。

### Tests for User Story 2

- [X] T016 [P] [US2] `training/tests/unit/test_recipe_split_unit.py` に back-compat canonicalization テスト(`race_count_v1`→recipe_hash が既存値と byte 一致[既存 hash 契約テスト緑で実証] / `race_day_v1`→hash が変化 / 不明値は construction で reject / `meta()` は audit 用に field 保持)。**9/9 緑**。
- [X] T017 [P] [US2] 同上ファイルに `assert_split_unit_compatible` の split-change 拒否テスト(pre-073 row=legacy 扱い・first save/同一 split は許可・split 変更は fail-closed)。
- [X] T018 [P] [US2] SC-005 を **artifact byte-invariance** で機械実証(active lgbm-063 の model/calibrator/preprocessor SHA-256 が freeze oracle と一致・serving コード未変更ゆえ予測は構造的に不変)。**PASS**。フル 16 頭 serving E2E は quickstart §1 の acceptance で実施(要 real DB serving predict)。

### Implementation for User Story 2

- [X] T019 [US2] `recipe.py` の `ModelRecipe` に `calibration_split_unit: str = "race_count_v1"` を追加・construction で値検証・`recipe_hash` を back-compat canonicalization(legacy 既定は hash から除外、`race_day_v1` のみ hash 変化)・`RecipeFactory.fit` から predictor へ伝播。research D1・data-model.md §1。
- [X] T020 [US2] `calibration.py` に `select_split_fn`/`CALIBRATION_SPLIT_UNITS` を追加、`predictor.py` の split 呼び出しを recipe 由来分岐に(`race_count_v1`→`split_train_by_time` で byte 一致 / `race_day_v1`→`split_train_by_day`)、`fit_info_`/metadata/`summary["training"]` に split unit を記録。`race_day_v1` 学習・昇格は本 feature では実行しない(FR-011)。
- [X] T021 [US2] legacy 凍結を `training/src/horseracing_training/legacy_freeze.py`(create-only disk artifact `freeze_073.json`)で実装 + 委任 guard `artifacts.assert_split_unit_compatible`。**設計変更(FR-011 遵守)**: active DB 行への書込を避けるため `metrics_summary` JSONB でなく **create-only disk artifact + 委任 committed copy `specs/073-eval-contract-correctness/legacy-freeze-lgbm-063.json`**(artifacts/ は gitignore のため committed oracle は spec 配下)。digest pin=model `1a85b035…`/calib `4babdda7…`/prep `cf1d518d…`。

**Checkpoint**: 既存 active が凍結され、split が recipe 明示化され、parity が守られる。

---

## Phase 5: User Story 3 - bootstrap を実体一致名にし過去 verdict を凍結 (Priority: P2)

**Goal**: bootstrap 名称が実体(開催日クラスタ)と一致し数値維持、block 幅感度が diagnostic として併記、068/069/070 verdict が不変履歴。

**Independent Test**: 改名後の数値が旧関数と完全一致、v2 感度が複数 block 幅で出る、過去 verdict が contract_version=v1 で上書きされない。

### Tests for User Story 3

- [X] T022 [P] [US3] `eval/tests/unit/test_bootstrap.py` に `race_day_cluster_bootstrap_ci_v1` の数値 golden(固定入力/seed で point/ci_low/ci_high を pin)。既存決定論テストと併せ数値不変を担保。**PASS**。
- [X] T023 [P] [US3] `eval/tests/unit/test_bootstrap.py` に v2 感度の label 集合(2d/3d/4d/week)・seed 決定論・coarser block で n_days 減少を assert。感度は gate に AND しない設計(diagnostic)。**PASS**。
- [X] T024 [P] [US3] `eval/tests/unit/test_gate_decision.py` に新 verdict=`v2` と `assert_verdict_immutable`(prior 有りは fail-closed=上書き/再分類禁止)を assert。**PASS**。

### Implementation for User Story 3

- [X] T025 [US3] `bootstrap.py` の `moving_block_bootstrap_ci` を `race_day_cluster_bootstrap_ci_v1` に改名(数値不変・docstring で cluster bootstrap 明記)、呼び出し元(`paired.py`×2・`test_bootstrap.py`×6)を全置換(deprecation alias なし)。research D4。
- [X] T026 [US3] `bootstrap.py` に `race_day_cluster_bootstrap_sensitivity_v2`(2/3/4 開催日 consecutive-block + ISO週)を diagnostic として追加、`paired_eval(compute_sensitivity=)` opt-in で `PairedReport.bootstrap_sensitivity` に添付。primary は不変。**meeting(会場)は day-keyed 入力に venue が無いため未実装(要 venue キー、明記)**。
- [X] T027 [US3] `decision.assert_verdict_immutable` を実装(prior verdict 有りは fail-closed)。`evaluation_contract_version` 付与は T014/PairedReport が担い、T027 は不変ガードのみ(D1)。

**Checkpoint**: bootstrap の誤称が是正され、過去判定が不変に保たれる。

---

## Phase 6: User Story 4 - 探索期間を development set として凍結 (Priority: P3)

**Goal**: 070 status matrix 凍結、2008–2026 を development evidence 明記、prospective holdout を DORMANT で事前登録(実計測なし)。

**Independent Test**: 070 が append-only supersession として固定・過去文書不変、2008–2026 明記、holdout が DORMANT で器のみ。

### Implementation for User Story 4

- [X] T028 [P] [US4] `docs/plan/070-status-freeze.md` を作成 — 070 status matrix(F03/F04/F05=rejected/unwired・revert commit `81f5d9e`・features-018 復帰・registry 実態)を commit 参照の append-only supersession として固定(過去文書は不変)。research D6・data-model §5。
- [X] T029 [P] [US4] `docs/plan/development-evidence.md` を作成 — 2008–2026 を development evidence と明記・confirmatory は unused data + 事前登録が必要と規定。
- [X] T030 [P] [US4] `docs/plan/prospective-holdout-preregistration.md` を作成 — DORMANT 事前登録フォーマット(state=DORMANT・hypothesis/formula/thresholds/primary_metric/stopping_rule・time_to_signal・start_preconditions)。実計測なし・憲法V改定前提を明記。data-model §6。

**Checkpoint**: 探索期間が正しく凍結され、将来の confirmatory の器が用意される。

---

## Phase 7: Polish & Cross-Cutting

- [ ] T031 `specs/073-eval-contract-correctness/quickstart.md` の受け入れ手順を実 DB で通し、SC-001〜SC-010 を確認。**FR-008 の「068 必須テスト突合」を具体化**: `specs/068-evaluation-contract-calibration/tasks.md` の未完了項目(started-all 統合=T012 対応・実 DB paired E2E を 2 回実行して一致・決定論確認=T007 対応・gate artifact 改変拒否テスト=T004 対応)を 1 項目ずつ check-off し、突合結果を quickstart に記録する(C2)。
- [X] T032 [P] 触った `eval/` `training/` ファイルの ruff/lint クリーン・既存スイート緑(eval 125/training 116)。**FR-020 schema-zero 検証済み**: db/ 変更ゼロ・migration head 不変(0011)・新 073 モジュールに `__tablename__` ゼロ・active 昇格ゼロ(E1)。
- [ ] T033 監査 artifact と CLI 出力が contracts/cli.md と一致することを最終確認(後方互換 `adopted` 併記含む)。**FR-011 no-write ガードを機械検証**(C1): 本 feature 実行中に `model_versions` の active 昇格・active artifact の上書きが発生しないこと(legacy 凍結レコードは append-only の追記のみ)を assert。SC-005 のバイト不変(観測)に加え「書き込み行為そのものが無い」ことを担保する。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: T001 は着手ブロッカー(先行必須)。T002 は T001 と並列可。
- **Foundational (Phase 2)**: Setup 後。US1/US3 をブロックする(監査骨格・fail-closed ローダ)。
- **User Stories (Phase 3–6)**: Foundational 後。US2 は US1 と独立に並行可(recipe/predictor は eval と別ファイル)。US4 は docs のみで他と完全独立。
- **Polish (Phase 7)**: 望む US 完了後。

### User Story Dependencies

- **US1 (P1)**: Foundational(T003/T004)必須。他 story 非依存。
- **US2 (P1)**: T001(active 確定)必須。eval を触らないため US1 と並行可。
- **US3 (P2)**: Foundational + US1 の監査 artifact(T014)に `evaluation_contract_version` を足すため T014 の後が安全。
- **US4 (P3)**: 完全独立(docs / specs のみ)。いつでも並行可。

### Within Each Story

- テストを先に書いて FAIL を確認 → 実装。
- US1: T003/T004(foundational)→ enum/結線(T010/T011)→ started-all(T012)→ ECE(T013)→ 監査出力(T014)→ CLI(T015)。
- US2: recipe(T019)→ predictor 分岐(T020)→ legacy 凍結(T021)。

### Parallel Opportunities

- T002 ∥ T001 後半。
- US1 のテスト T005–T009 は相互並列。US2 テスト T016–T018 も並列。US3 テスト T022–T024 も並列。
- **US2 全体は US1 と並行**(別パッケージ)。**US4 は常時並行**(docs)。

---

## Parallel Example: User Story 1 tests

```bash
Task: "eval/tests/test_gate_decision.py 三値真理値表"
Task: "eval/tests/test_gate_boundaries.py 9d/10d・空window・subgroup不足"
Task: "eval/tests/test_determinism.py 2回実行 <1e-9"
Task: "eval/tests/test_started_all_harness.py harness==paired started-all"
Task: "eval/tests/test_eval_leak_guard.py 派生値の特徴流入なし"
```

---

## Implementation Strategy

### MVP First (US1 + US2 = P1)

1. Phase 1 Setup(**T001 で active を DB 確定**)
2. Phase 2 Foundational(監査骨格・fail-closed ローダ)
3. Phase 3 US1(三値 gate・started-all・ECE・監査・決定論)→ **STOP & VALIDATE**
4. Phase 4 US2(split recipe 化・legacy 凍結・**SC-005 parity**)→ 並行実施可

US1+US2 で「正しい物差し + parity 保証」という基盤 MVP が完成。

### Incremental Delivery

1. Setup + Foundational → 基盤
2. US1 → 単独検証(三値・決定論・監査)
3. US2 → 単独検証(recipe_hash・parity 16頭 mismatch 0)
4. US3 → bootstrap 改名の数値一致・過去 verdict 凍結
5. US4 → 070 凍結・dev evidence・dormant(docs のみ、いつでも)

---

## Notes

- **再学習・昇格・active 書換なし**。`race_day_v1` の学習は別 feature(Day-split Retraining)。
- **触らない**: `probability/`(校正リーク=074)・`api/`/`front/`/`admin/`(realized 改名=075)・DB schema/migration・serving。
- 校正リーク前提の two-gamma/stage discount 後 ECE は 074 完成後(FR-007)。
- コミットは task 単位。各 checkpoint で story 単独検証。
- gate-config は OOS 結果を見た後に変更しない(III)。過去 verdict(068/069/070)は不変(FR-015)。
