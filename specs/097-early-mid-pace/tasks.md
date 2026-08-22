# Tasks: Early-Mid Pace Features (rel_early_mid)

**Input**: spec.md / plan.md / research.md(D1-D10) / codex-review.md / data-model.md /
contracts(feature-columns INV-EM1..EM8・adoption-gate) / gate-config.json(凍結 hash 6dd6a013…=analyze 反映後の再事前登録値)

**テストは必須**(憲法 II/III: リーク境界と評価先行は挙動テストで固定する)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列可(別ファイル・未完了タスクに依存しない)
- **[Story]**: US1=構築 / US2=判定 / US3=後始末

## Path Conventions

- features: `features/src/horseracing_features/`・テスト `features/tests/unit/`
- eval: `eval/src/horseracing_eval/`・training: `training/src/horseracing_training/`
- scripts: `scripts/`・証跡: `specs/097-early-mid-pace/`

---

## Phase 1: Setup

- [ ] T001 `specs/097-early-mid-pace/gate-config.hash.txt` の hash(`6dd6a013…`= codex tasks レビュー + analyze 反映後の再事前登録値)が `gate_config_hash(gate-config.json)` と一致することを確認し、以降 gate-config.json を変更しない
- [ ] T002 実 DB で前提を再確認して `specs/097-early-mid-pace/evidence-preflight.md` に記録: active=lgbm-094-cap900・feature_version=features-021・feature_hash=663fe86c…(metadata.json)・registry 138 列・`race_results.finish_time/last_3f` の 2026 充足 ≥99%

---

## Phase 2: Foundational(全 US の前提)

- [ ] T003 `eval/src/horseracing_eval/paired.py`: `PairedReport` に `diffs_by_day: dict[str, list[float]]` を追加し `to_dict()` に含める(既存フィールド不変・既存テスト無改修で緑)。3 カットオフの pooled CI を driver で取るために必要。`eval/tests/unit/` に「既存レポートの to_dict キーが純増である」テストを追加。併せて `eval/src/horseracing_eval/provenance.py` を新規: `frame_projection_hash(df, cols) -> str`(決定論・行順非依存・pure pandas、features を import しない=020 境界)。driver と T019 はここから import する(テストモジュールから本番 assert を import しない)
- [ ] T004 `features/src/horseracing_features/pace_features.py` の `_pace_runs` / `_rolling_asof` が他モジュールから import 可能であることを確認(private 名のまま再利用する。二重実装禁止=025 単一 as-of 源)。変更不要なら本タスクは確認のみ

---

## Phase 3: User Story 1 — 一貫した早め区間ペース特徴の構築 (P1) 🎯 MVP

**Goal**: `asof_rel_early_mid_avg` / `_best` を features-022 として結線し、共有 138 列バイト不変・
リーク境界・充足を実 DB で証明する。

**Independent Test**: T013(parity)+T014(coverage)+T010-T012(単体)が緑なら US1 単独で完了。

### Tests for User Story 1

- [ ] T005 [P] [US1] `features/tests/unit/test_early_mid_pace_features.py`: 手計算 fixture(INV-EM8)— 3 頭×3 レースの合成 Frames で `em = time_s − last3f_s`、レース内完走馬平均との差、直近 5 走 mean/min を手で計算した厳密値と `assert_frame_equal(check_exact=True)`。秒単位で意味のある規模(例: 70.5−35.2)を使う
- [ ] T006 [P] [US1] 同ファイル: 欠損規則(INV-EM4)— finish_time 欠損 / last_3f 欠損 / em ≤ 0(入力破損)/ 過去完走ゼロ の各ケースで NaN。0 埋め・平均埋めが無いことを assert
- [ ] T007 [P] [US1] `features/tests/unit/test_early_mid_pace_leak.py`: リーク 3 方向(INV-EM3・023 の `test_pace_features_leak.py` を雛形に)— (a) 対象レースの結果変更 (b) 同日他レースの結果変更 (c) 未来レースの結果変更 → 対象行の 2 列が不変
- [ ] T008 [P] [US1] `features/tests/unit/test_early_mid_pace_features.py`: INV-EM7(1200m 恒等)— distance=1200 の fixture で走単位 `rel_em == rel_first3f`(両方非欠損の行で厳密一致)。INV-EM2(独立性)— `first_3f` 列を全 NaN にしても新列の値が不変
- [ ] T009 [P] [US1] `features/tests/unit/test_registry_features022.py`(`test_registry_features021.py` を雛形に): 2 列が REGISTRY に 1 回ずつ・group `early_mid_pace` のメンバーが厳密にその 2 列・`materialized_columns()` に含まれる・列順決定論・`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-022"]["features-021"] == "663fe86c…"`(T002 で実測した完全 hash)

### Implementation for User Story 1

- [ ] T010 [US1] `features/src/horseracing_features/early_mid_pace_features.py` を新規作成: `build_early_mid_pace_features(frames, *, target_race_ids=None) -> DataFrame[race_id, horse_id, asof_rel_early_mid_avg, asof_rel_early_mid_best]`。`_pace_runs(frames)` を呼び `em_s = time_s − last3f_s`(em_s ≤ 0 → NaN)、完走馬の `race_mean_em`、`rel_em = em_s − race_mean_em` を追加し、`_rolling_asof(fin, targets, {"asof_rel_early_mid_avg": ("rel_em","mean"), "asof_rel_early_mid_best": ("rel_em","min")})` で集約。全列 float64。docstring に D2(近線形冗長)と INV-EM1/2 を明記
- [ ] T011 [US1] `features/src/horseracing_features/registry.py`: REGISTRY に 2 列(`FeatureMeta("early_mid_pace", _T.PRE_ENTRY, _M.NULL)` 相当・timing は既存 pace 列と同じ)、`FEATURE_GROUPS` に `early_mid_pace`、`FEATURE_VERSION = "features-022"`、`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-022"] = {"features-021": <T002 実測 hash>}`(コメントに「lgbm-094-cap900 の metadata.feature_hash を 2026-08-22 に実測」と根拠を書く)
- [ ] T012 [US1] `features/src/horseracing_features/materialize.py`: `_OPTIONAL_LEAF_BLOCKS["early_mid_pace"] = (2 列)` を追加し、`build_asof_features` の最後段(全 consumer の後=真の leaf)で `build_early_mid_pace_features(frames, target_race_ids=target_race_ids)` を left-merge。`skip_blocks={"early_mid_pace"}` で features-021 と同一列集合になること
- [ ] T013 [US1] `scripts/parity_097.py`(`parity_088.py` を雛形に): 単一プロセス・単一スナップショットで `skip_blocks=∅` と `skip_blocks={"early_mid_pace"}` の二重ビルドを行い、共有 138 列全量 `assert_frame_equal(check_exact=True, check_dtype=True)`。結果(行数・列数・mismatch 0)を `specs/097-early-mid-pace/evidence-parity.md` に記録(SC-001 / INV-EM5)
- [ ] T014 [US1] `scripts/coverage_097.py`: 年別×列別の充足率(全行・`has_past_race` 行)と、2026 の `has_past_race` 行で ≥95% を assert(SC-002)。`evidence-coverage.md` に記録
- [ ] T015 [US1] 072 投影パリティ: `features/tests/unit/test_early_mid_pace_features.py` に `target_race_ids` 指定ビルドが full ビルドの該当行と `check_exact` 一致するテストを追加(per-horse 型)
- [ ] T016 [US1] `features materialize` を features-022 で実行し manifest の `source_fingerprint` が 021 の manifest と一致(新規ソース列ゼロ=INV-EM6)・`feature_version=features-022` を確認。結果を `evidence-preflight.md` に追記
- [ ] T017 [P] [US1] 表示ラベル: `front/src/components/featureLabels.ts` と `admin/src/lib/featureLabels.ts` に 2 列の日本語ラベル(例「前中盤ペース 平均」「前中盤ペース 最速」)を**同一内容で**追加(088 の教訓: `test_display_label_coverage` が要求)。front/admin のテストが緑
- [ ] T018 [US1] serving 配線 E2E smoke: 実 DB で `predict --race-id <2026 の任意レース>` を features-022 registry 下で実行し、(a) active lgbm-094 が compat 経路(`reg=features-021` マーカー)でロードされ (b) 予測 win_prob が bump 前の永続化値と **18 頭バイト一致**(091 T 配線 smoke の同型)。結果を `evidence-preflight.md` に追記

**中断点**: T013 で mismatch>0 なら即中断(既存列を動かした=設計違反)。

---

## Phase 4: User Story 2 — 事前登録ゲートでの採否判定 (P2)

**Goal**: 凍結 gate-config の下でシミュレーション primary + guard 2 本を 1 回実行し、単一式で
verdict を出す。

**Independent Test**: `verdict.json` が `artifact_kind=counterfactual_supply_simulation`・
`eligible_for_verdict=False`・`feature_adoption_eligible=True`・hash 3 点・
`verdict ∈ {ADOPT, REJECT, NO_DECISION(実行不能のみ)}` を持ち、quickstart §2 の出力形と一致。

### Tests for User Story 2

- [ ] T019 [P] [US2] `training/tests/unit/test_097_mask.py`: マスク SQL の単体テスト — 合成 DB 行で「cutoff 以降 ∧ distance≠1200 の first_3f のみ NULL、1200m と cutoff 前は不変、他列不変、行数不変」を assert(contracts/adoption-gate の対称性の一部)。加えて `horseracing_eval.provenance.frame_projection_hash` を `(race_id, horse_id, race_date, distance, first_3f)` 射影で単体テスト(行順入替で不変・1 値変更で変化)(codex tasks Q2)
- [ ] T020 [P] [US2] `eval/tests/unit/test_paired_report_diffs_by_day.py`: T003 の露出フィールドで pooled CI が単窓 CI と整合する(1 窓だけ渡せば既存 `bootstrap_ci` と一致)ことを固定

### Implementation for User Story 2

- [ ] T021 [US2] `scripts/097_simulated_supply_gate.py` を新規作成。`assert_confirmatory(cfg, expected_hash, eval_window)` を冒頭で通し、3 カットオフを順次: (1) `s.rollback()` (2) マスク UPDATE(未 commit) (3) **対称性 assert**: マスク前後で `load_eval_races` の race_id 集合・各レースの started 集合・winner が同一(破れたら即 fail) (3b) **マスク provenance assert**(codex tasks Q2): features の loader で Frames を読み、cutoff 以降∧距離≠1200m の first_3f 非 NULL が **0 行**であること、両アームの build が同一 Frames hash を見ていること、両アームとも `use_materialized=False`(parquet は非マスク世界の凍結物)を assert (3c) **単一ロード**(analyze P3): マスクを当てたセッション `s` で `LightGBMPredictor(s, ...)._ensure_data()` を **1 回だけ**呼んで `TrainingMatrix` を作り、両アームの `CalibSplitFactory._shared` に同一オブジェクトを注入する(fold 内での再ロード禁止・`is` で同一性 assert)。provenance hash はこの matrix の元 Frames 射影で取る。`eval_window` は `cfg["eval_window"]` をそのまま渡し、`scored_windows ⊂ envelope` を assert(analyze C7) (4) 候補 factory = `CalibSplitFactory(ModelRecipe(pl_topk, calibration none, params n_estimators=900, weight_mask 0.5/20260810, seed 42), n_oof_blocks=8, isotonic)` / 基準 = 同レシピ + `drop_features=FEATURE_GROUPS の early_mid_pace 展開` (5) `paired_eval(... first_valid_year=窓年, valid_from=窓始, subgroups=False, num_threads=1)` (6) `diffs_by_day` を回収 (7) `s.rollback()`。採点窓は gate-config の `scored_windows` を読む(ハードコード禁止)。**出力規律**(codex tasks Q1b): primary・guard1・guard2 の全成分が揃うまで効果の数値を stdout にも JSON にも出さない(進捗は「cutoff X: build OK / symmetry OK / provenance OK / fit OK」のみ)
- [ ] T022 [US2] 同 driver: 3 窓の `diffs_by_day` を union(互いに素を assert)→ `race_day_cluster_bootstrap_ci_v1` → `inflate_for_seed_noise(sd_fold, n_folds=3)` → `recent_window_guard(pooled_diffs_by_day, cfg=cfg, max_date=2024-12-31)`(gate-config `recent_guard` の凍結値。暦日で 3y 窓=2022/2024 の採点日・5y 窓=3 窓全部) → `evaluate_core_gate(diff, ci_low, ci_high(総), recent=その結果, top2/top3/ECE は 3 窓 pooled, cfg)` で v4 標準式を適用。**新しい判定式を書かない**
- [ ] T023 [US2] 同 driver: guard 1(full-info)— マスク無しで同 3 窓を同手順で実行し pooled diff に `three_way(ci_low, ci_high, margin=0.003)` → FAIL なら guard1=False。guard 2 — 実窓 2025-10-11..最新(マスク無し・first_valid_year=2025・valid_from=2025-10-11)の paired diff に `ci_low > 0.005` で FAIL
- [ ] T024 [US2] 同 driver: verdict JSON を `specs/097-early-mid-pace/verdict.json` に書く: `evaluation_contract_version="v4"`・`artifact_kind="counterfactual_supply_simulation"`(codex tasks Q1d: full_walk_forward だと `evaluate_promotion` がこれで ACTIVE 昇格を通す構造的な穴 → 専用種別で昇格ゲートから弾く。列採用の適格性は別キー `feature_adoption_eligible=True`)・`eligible_for_verdict=False`(モデル昇格には使えない)・`evidence_regime="masked_pseudo_supply_death"`・gate_config_hash・両アーム recipe_hash・各カットオフの race_id_set_hash・マスク定義・採点窓・pooled point/CI(標本・総)・guard 2 本の詳細・`verdict` と式 `primary_pooled AND guard1 AND guard2`・「反実仮想頑健性(counterfactual robustness)として報告」の注記
- [ ] T025 [US2] smoke(配線のみ): gate-config `smoke` 節の**事前登録外カットオフ 2016-01-01・採点窓 2017**(codex tasks Q1a: 実カットオフの smoke は確認データの中間閲覧になる)・rounds=50・blocks=2・b=200 で driver を通し、対称性・provenance・JSON 形を確認。**効果の数値は stdout/JSON とも非表示**(`redact_effect_numbers`)。`artifact_kind="smoke"` で `out/` に出す(追跡しない)
- [ ] T026 [US2] 本実行: quickstart §2 のコマンドで nohup 実行(推定 ~4.5h・実績比)。完了後 `verdict.json` をコミット。**個別カットオフの数値を見て解釈を変えない**(pooled が正本)
- [ ] T027 [US2] 結果を `spec.md` 末尾の「実測結果」節に転記(primary pooled・guard1・guard2・verdict・限界 D10 の再掲)

**verdict 分岐**(中断ではない=analyze O2): guard 1 が FAIL なら verdict=REJECT として Phase 5 の REJECT 分岐へ。実行は全成分を完走させ、verdict は最後に 1 回だけ評価する(出力規律)。

---

## Phase 5: User Story 3 — 判定に従った後始末 (P3)

**Goal**: verdict に応じて repo を終端状態にする。どちらの分岐でも active 予測は不変。

**Independent Test**: REJECT= T029 のバイト一致+T030 の保全テスト緑 / ADOPT= T032 の候補登録。

### REJECT 分岐

- [ ] T028 [US3] `registry.py` の FEATURE_VERSION を features-021 に戻し、2 列の REGISTRY/FEATURE_GROUPS/compat pin エントリを削除。`materialize.py` の leaf block と結線を削除。`test_registry_features022.py` を削除。front/admin のラベル 2 行を削除(表示専用なので残しても害は無いが、model-input 列でないものにラベルを残さない規律)
- [ ] T029 [US3] 実 DB で `predict --race-id <T018 と同じレース>` を再実行し、revert 後の予測が T018 の値と 18 頭バイト一致(SC-005)。`evidence-preflight.md` に追記
- [ ] T030 [US3] `early_mid_pace_features.py` + `test_early_mid_pace_features.py` + `test_early_mid_pace_leak.py` を**非結線で保全**(062/070/090 同型)。テストは `build_early_mid_pace_features` 直呼びで緑であることを確認。モジュール docstring 冒頭に「REJECT(日付・verdict.json 参照)・非結線保全」を追記
- [ ] T031 [US3] `features materialize` を features-021 で再実行し parquet を戻す(manifest の feature_version 確認)

### ADOPT 分岐

- [ ] T032 [US3] 実データ(マスク無し)で候補モデルを学習・登録: `register-arm-e --n-estimators 900 --artifacts-dir <絶対パス>`(weights_uri 相対パス事故の回避。**`--verdict` 引数は存在しない**=analyze C1。register-arm-e は常に CANDIDATE 登録)。続けて `promote-model --model-version <登録名> --verdict ../specs/097-early-mid-pace/verdict.json`(**--apply 無し=dry-run**)を実行し、`verdict_artifact_not_eligible` で昇格が**構造的に拒否される**ことをログで確認して `evidence-preflight.md` に貼る。証拠と学習レジームの違い(`evidence_regime=masked_pseudo_supply_death` / `model_training_regime=real_unmasked`)は verdict.json 側に記録済み(T024)。**昇格しない**(contracts/adoption-gate: 標準窓非劣化+本 verdict+prospective の 3 点セットで別段)
- [ ] T033 [US3] 候補モデルで標準窓(2019-2024)の対 active 非劣化を確認評価(別 gate-config・新規事前登録)。結果を spec に転記。prospective 監視の開始日と判定日を `specs/097-early-mid-pace/prospective.md` に凍結

---

## Phase 6: Polish & Cross-Cutting

- [ ] T034 [P] `CLAUDE.md` の SPECKIT 区間を**手動 Edit**で「実装完了・verdict」に更新(hook 実行禁止)
- [ ] T035 [P] memory: `first3f-per-horse-unavailable` に 097 の verdict と数値を追記。ADOPT なら `feature-097-early-mid-adopt` を新規
- [ ] T036 全パッケージ(features/eval/training/serving/front/admin)のテストと ruff を実行し緑を確認。`api` の chaos p95 既知事象は対象外と明記
- [ ] T037 コミット・push。commit 本文に pooled 数値・guard・verdict・限界(D10)を残す

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 → Phase 2 → Phase 3(US1)→ Phase 4(US2)→ Phase 5(US3・分岐)→ Phase 6
- US2 は US1 完了(T013 parity PASS)が前提。US3 は US2 の verdict が前提

### Within US1

- T005-T009(テスト・並列)→ T010(モジュール)→ T011(registry)→ T012(結線)→ T013(parity・**中断点**)→ T014/T015/T016/T017(並列可)→ T018(E2E smoke)

### Within US2

- T019/T020(並列)→ T021 → T022 → T023 → T024 → T025(smoke)→ T026(本実行 ~4.5h)→ T027

### Parallel Opportunities

- US1: T005・T006・T007・T008・T009 は全て別ファイル/別関数で並列。T014・T015・T016・T017 も並列
- US2: T019・T020 並列。guard 2 本(T023)は primary(T021-22)と独立に実行可能だが、driver は 1 本にまとめる(hash・窓の検査を 1 箇所にするため)

## Implementation Strategy

**MVP = US1**。US1 が parity+leak+coverage で閉じれば、列の構築自体は安全に完了している。
US2 は 1 回の長時間実行で、結果に関わらず US3 で終端する。**US2 の結果を見てから US1 の列集合を
変えることは禁止**(OOS 後の列選別 = 068 C2)。
