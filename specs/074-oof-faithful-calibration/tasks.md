---
description: "Task list for feature 074 — OOF-faithful Calibration Evidence"
---

# Tasks: OOF-faithful Calibration Evidence

**Input**: Design documents from `/specs/074-oof-faithful-calibration/`

**Prerequisites**: plan.md, spec.md, research.md (D1–D7), data-model.md, contracts/cli.md, gate-config.json, quickstart.md

**Tests**: 含める(憲法 II/III/V が leak-guard・OOS・parity・監査 test を必須化)。

**Organization**: user story 単位。US1/US2=P1、US3/US4=P2。**依存順**: US2(attestation)→ US1(OOF 生成)→ US3(校正再検証)/ US4(manifest)。

**制約**: スキーマ変更ゼロ・migration なし・**production 非結線**(serving/betting/api/db/front 不変)。全 artifact は content-addressed disk(`artifacts/oof/`、prediction_runs 非保存)。触るのは `probability/` と `training/`(+ `eval/foldfit` 再利用)。

**計算コスト注記**: OOF 生成は fold ごと再学習=**長時間 job**(pl_topk フル walk-forward で十数時間級)。`--smoke`(小 fold)で実装可否ゲート、フルは operator job。⏳ マークは長時間タスク。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 前提確認: 実 DB で active=`lgbm-063`(features-017)を再確認、073 freeze oracle(`specs/073-eval-contract-correctness/legacy-freeze-lgbm-063.json`)存在、073 の calibrated-stage ECE(FR-007)が未完=074 前提であることを `quickstart.md` §0 に記録。
- [X] T002 [P] `specs/074-oof-faithful-calibration/gate-config.json` を v2 事前登録(three_way verdict・prequential fit_scope・transfer_check・strictly-later ECE・OOS 前固定, III)。**plan 段で作成済み**。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: OOF bundle の content-addressed 読み書き基盤(US1/US3/US4 が共有)。**⚠️ 完了まで US1 以降を始めない**。

- [X] T003 `probability/src/horseracing_probability/oof_bundle.py`(新)に OOF bundle の content-addressed 直列化/読込を実装(data-model §2: predictions・fold per-fold hash・oof_race_set_hash・prediction_checksum・attestation_digest・bundle_digest)。atomic write(temp→rename)・canonical payload の SHA-256。DB 非依存。
- [X] T004 `probability/src/horseracing_probability/oof_bundle.py` に bundle 検証(schema/version・checksum 照合・世代不一致 fail-closed)を追加。

---

## Phase 3: User Story 2 - legacy recipe 完全 attestation (Priority: P1)

**Goal**: lgbm-063 の完全 resolved recipe attestation を固定し、recipe-faithful factory を復元可能にする。

**Independent Test**: attestation が data-model §1 の全フィールドを含み、欠落/差異で fail-closed、これから復元した recipe が attestation と整合。

### Tests for User Story 2

- [X] T005 [P] [US2] `training/tests/unit/test_legacy_attest.py` に attestation の全必須フィールド存在・content-addressed digest 決定論・フィールド欠落/差異で fail-closed(または新 digest)を assert。

### Implementation for User Story 2

- [X] T006 [US2] `training/src/horseracing_training/legacy_attest.py`(新)を実装: 073 freeze + `artifacts/model_versions/lgbm-063/metadata.json` から resolved LightGBM params・objective/postprocess・ordered feature columns+feature_version・TE 列/smoothing・internal calibration(method/calib_frac/split_unit)・seed/threads・drop list・source_fingerprint/materialized_hash・code_sha を集約し content-addressed attestation artifact 化(data-model §1)。
- [X] T007 [US2] `legacy_attest.py` に attestation → `ModelRecipe`(073 拡張)+ resolved params → recipe-faithful `RecipeFactory` 構築関数を実装(欠落フィールド fail-closed)。

**Checkpoint**: recipe-faithful factory が attestation から構築でき、US1 の OOF 生成が着手可能。

---

## Phase 4: User Story 1 - OOF-faithful sample 生成 (Priority: P1) 🎯 MVP

**Goal**: fold ごと strict-past 再学習の recipe-faithful OOF prediction を content-addressed bundle として生成。対象結果を見ない。

**Independent Test**: 全 OOF race で `max(train_date)<race_date`、同日除外、結果変更で不変、bundle digest 安定、byte 決定論。

### Tests for User Story 1

- [X] T008 [P] [US1] `training/tests/integration/test_oof_strict_past.py` に全 OOF race で booster/内部校正/TE の `max(train_date) < race_date` を assert(SC-001)。
- [X] T009 [P] [US1] 同日 train/valid 混入は **expanding folds が年単位**=T008 の `train_through 年 < valid 年` assertion で構造的にカバー(同日 train/valid ペアは発生不能)。SC-002 は US1 統合で担保。
- [X] T010 [P] [US1] `training/tests/integration/test_oof_result_invariance.py` に対象レース結果を変更しても当該 OOF prediction がバイト不変・result hash のみ変化を assert(SC-003, leak-guard)。
- [X] T011 [P] [US1] `training/tests/integration/test_oof_digest_stable.py` に別モデル/full-history latest run を DB に追加しても bundle digest 不変(SC-004)、2 回生成で byte 一致(SC-005)を assert。

### Implementation for User Story 1

- [X] T012 [US1] `training/src/horseracing_training/oof_generate.py`(新)を実装: recipe-faithful factory(T007)を `eval/foldfit.predict_over_folds` に通し per-race OOF prediction を得て `oof_bundle`(T003)へ直列化。同日除外は `race_date<target_date`(FR-003)。
- [X] T013 [US1] `training/src/horseracing_training/cli.py` に `oof-generate` サブコマンド(`--base-model-version`/`--from`/`--to`/`--first-valid-year`/`--seed`/`--num-threads`/`--out`/`--smoke`)。read-only・atomic publish。contracts/cli.md。
- [X] T032 [US1] **features-017 gap 解決(research D9・T014 の前提・codex レビュー MUST)**: `predictor.py` に `restrict_features`(inclusion・_ensure_data で fail-closed filter・order 保存・restrict=None で byte 不変)、`recipe.py` RecipeFactory に restrict_features field、`legacy_attest.py` に `factory_from_attestation`(ordered_feature_columns で制限)、`oof_generate` を差し替え。テスト=restrict で features-017 列のみ・missing で fail-closed・restrict=None 後方互換。069 の additive パリティ再利用で byte-faithful。
- [ ] ⏳ T014 [US1] `--smoke`(小 fold・合成 or 小窓)で OOF 生成を実行し T008–T011 を緑にする(**実装可否ゲート**)。フル 2008–2026 生成は operator 長時間 job(quickstart §2、セッション外)。

**Checkpoint**: OOF-faithful bundle が smoke で生成・検証できる=リーク是正の本体が成立。

---

## Phase 5: User Story 3 - OOF 上で two-gamma/stage λ 再検証 (Priority: P2)

**Goal**: 校正 sample を OOF bundle に差し替え、prior OOF prequential fit・strictly-later OOF block で calibrated-stage ECE・048 採否を OOF で測り直す。

**Independent Test**: fit は prior OOF のみ、ECE は strictly-later block、verdict は三値、transfer-check ミスマッチ=NO_DECISION。

### Tests for User Story 3

- [X] T015 [P] [US3] `probability/tests/integration/test_oof_calibration.py` に two-gamma/λ が prior OOF fold のみで fit・fit fold を評価 CI に含めない(prequential)ことを assert。
- [X] T016 [P] [US3] 同上に ECE が strictly-later OOF block で測られ fit sample では測らない(SC-007)、verdict が ADOPT/REJECT/NO_DECISION、transfer-check ミスマッチ=NO_DECISION を assert。
- [X] T017 [P] [US3] 073 の既存 verdict/result が上書き・再分類されないこと(SC-009)を assert。

### Implementation for User Story 3

- [X] T018 [US3] `probability/src/horseracing_probability/model_calibration.py` の校正 sample source を **OOF bundle 差し替え**に対応(`load_p_samples` 経路に bundle 入力を追加、既定は現行=後方互換)。加えて `_latest_run_predictions` に `base_model_version` フィルタ(defense-in-depth)。
- [X] T019 [US3] `probability/src/horseracing_probability/model_calibration.py` に prequential fit(prior OOF のみ)+ transfer-check(OOF→full-history 分布ミスマッチ→NO_DECISION/fallback)を実装(gate-config 参照)。
- [X] T020 [US3] calibrated-stage ECE(two-gamma 後 win / stage discount 後 top2/top3)を 073 の帯別 ECE で strictly-later OOF block に適用。stage discount は win 非適用(win 不変)。
- [X] T021 [US3] `training/src/horseracing_training/cli.py` に `calibrate-oof` サブコマンド。`evaluation_contract_version=v2` append-only evaluation artifact を出力(data-model §3)。073 FR-007 を参照 fulfill(過去 verdict 不変)。contracts/cli.md。
- [ ] ⏳ T022 [US3] smoke bundle で `calibrate-oof` を実行し 048 の OOF verdict を artifact に記録(フル窓は operator job)。

**Checkpoint**: 048/049 の採否が OOF-faithful に測り直され、073 FR-007 の calibrated-stage ECE が evidence として存在。

---

## Phase 6: User Story 4 - 最小 content-addressed manifest (Priority: P2)

**Goal**: attestation + OOF bundle + evaluation を byte 再現可能に束ね、create-only/fail-closed で守る。

**Independent Test**: manifest が完全情報(full 精度 γ/λ 含む)を持ち、改竄/partial/未知 schema/世代不一致=拒否、同 payload=冪等。

### Tests for User Story 4

- [X] T023 [P] [US4] `training/tests/unit/test_calib_manifest.py` に manifest 完全情報(§US4 列挙・**full 精度 γ/λ**・checksum 群)存在、同 payload=同 digest(冪等)、同 key 異内容=conflict、改竄/partial/未知 schema/世代不一致=load 前拒否、identity fallback も明示 artifact を assert(SC-008)。

### Implementation for User Story 4

- [X] T024 [US4] `training/src/horseracing_training/calib_manifest.py`(新)を実装: attestation/bundle/evaluation を統合した content-addressed manifest(data-model §4)。create-only・atomic publish(temp→rename)・wall-clock/自己 digest は hash 対象外・full 精度 γ/λ。
- [X] T025 [US4] `training/src/horseracing_training/cli.py` に `verify-manifest` サブコマンド(改竄/世代不一致 fail-closed・冪等)。contracts/cli.md。

**Checkpoint**: OOF/校正 evidence が immutable manifest で再現可能に固定。

---

## Phase 7: Polish & Cross-Cutting

- [X] T026 [P] `model_internal_win_parity`(SC-006): 074 が serving/persisted 予測を触らないことを **artifact digest 不変**で静的確認(lgbm-063 の model/calibrator/preprocessor digest = 073 freeze oracle と一致)。任意で 1 レース runtime spot-check(16 頭 mismatch 0)を併記。
- [X] T027 [P] production 非結線を検証(SC-010): 074 の新規/変更コードが serving/betting/api を import せず・既存 PredictionRun/Recommendation を書かないことを境界 test で assert(FR-015)。
- [X] T028 [P] leak-guard: OOF/校正の派生値(ECE/γ/λ/verdict)がモデル特徴に還流しないことを assert(FR-018, II)。
- [X] T029 [P] schema-zero 検証: db/ 変更ゼロ・migration head 不変・新モジュールに `__tablename__` ゼロ(FR-017)。
- [X] T030 ruff/lint クリーン・既存スイート(probability/training/eval)緑(回帰なし)。
- [X] T031 066 dispersion / joint calibration の同型 leak(research D7 に記載済)を、074 の **最終 evaluation artifact の diagnostics セクション**に転記(是正は 076・本 feature では結線しない)。research の重複でなく成果物へ集約。

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup(1)**: T001 前提確認・T002 済。
- **Foundational(2)**: oof_bundle 基盤。US1 以降をブロック。
- **US2(3)**: attestation。**US1 の前提**(recipe-faithful factory)。
- **US1(4)**: OOF 生成。US2 後。US3/US4 の前提。
- **US3(5)**: 校正再検証。US1 bundle 後。
- **US4(6)**: manifest。US2/US1/US3 を統合。
- **Polish(7)**: 全 US 後。

### Within
- テスト先書き→実装。attestation(US2)→ OOF 生成(US1)→ 校正(US3)/ manifest(US4)。
- ⏳ smoke で実装可否ゲート → フル生成は operator 長時間 job。

### Parallel
- 各 story のテスト群は相互並列。US4 の manifest テストは bundle/evaluation 形が固まれば並行可。Polish T026–T029 は並列。

---

## Implementation Strategy

### MVP(US2 + US1 = P1、smoke まで)
1. Setup + Foundational(oof_bundle)
2. US2 attestation → US1 OOF 生成、**`--smoke` で T008–T011 緑**(実装可否ゲート)
3. これで「OOF-faithful sample が作れる」=リーク是正の中核 MVP

### Incremental
- US3 校正再検証(smoke verdict)→ US4 manifest → Polish。
- **フル 2008–2026 OOF 生成は operator 長時間 job**(セッション外)。実装・テストは smoke で緑にし、フルは運用で回す。

### 長時間 job の扱い
- ⏳ T014/T022 はフル窓ではセッション外。smoke で正しさを担保し、フルは nohup + 監視(perf-training-eval メモの前例)。

---

## Notes
- **production 非結線**: activation(推薦/serving が immutable artifact を読む)は **076**、realized 改名は **075**、global registry(save_model_version 上書き廃止)は **077**。
- 073 過去 verdict 不変・073 FR-007 は 074 artifact 参照で fulfill。
- gate-config は OOS 結果を見た後に変更しない(III)。
- 2008–2026 OOF ECE は development evidence(confirmatory でない)。
