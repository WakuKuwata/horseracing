---
description: "Task list for OOF Calibration Manifest Generation (078)"
---

# Tasks: OOF Calibration Manifest Generation

**Prerequisites**: spec.md, research.md (D1–D9), plan.md. **Tests included** (leak/parity/determinism/gate).
**Scope guard**: db/migration/API 不変。manifest は disk artifact のみ。

## Phase 1 — US1: stage-λ の OOF-faithful 評価機構 (P1)

- [X] T001 `load_topk_samples_from_oof(session, bundle)`(`probability/src/horseracing_probability/model_calibration.py`): `load_p_samples_from_oof` を mirror。p_dict=bundle OOF win、着順=`_placed_finishers`。返り値 `[(race_id, race_date, p_dict, (id1|None,id2|None,id3|None))]` を (race_date,race_id) sort。**新 DB 読取は Race.race_date と RaceResult のみ**(結果は label 限定・II)。
- [X] T002 [P] `load_topk_samples_from_oof` 単体テスト(`probability/tests/unit/test_load_topk_oof.py`): bundle 由来 p が使われる・**dead-heat 行列**(D4: 1着同着→(None,·,·)/2着同着→id2 None/3着同着→id3 None)・**リーク不変**(held-out 年の結果を変えても他年 sample 不変)・決定論 sort・p_dict は started のみ。
- [X] T003 `calibrate_stage_oof(session, bundle, *, gate_config)`(`probability/src/horseracing_probability/oof_calibration.py`): `calibrate_oof` を stage 版に。**raw OOF win で**(D1)fold ごと prior-only prequential λ2/λ3 fit(既存 `fit_stage_discount` core 再利用)・strictly-later block 収集。**dead-heat 契約**(D4): λ2=1着+2着一意/λ3=1〜3着一意(2着同着は λ3 も除外)。**fit label(exact 2nd/3rd 条件付き NLL)と ECE label(全 started の multi-positive y_top2/y_top3)を分離**(D4)。
- [~] T004 hardened stage gate: **DONE via 049 reuse** — `calibrate_stage_oof` runs the pre-registered `evaluate_stage_discount`/`decide_gate`(atomic top2+top3 LogLoss **AND** ECE 改善 + fold 多数決 + worst-fold top3 dLogLoss guard)over a `_BundlePredictor`. tri-value verdict(fitted-λ 無し→NO_DECISION)・win_identical 構造保証。**残 D3 refinement**: paired race-day bootstrap CI は 049 gate に無い追加要素で未実装(fold-majority+worst-fold で代替)。
- [X] T005 [P] `calibrate_stage_oof` 単体テスト(`probability/tests/unit/test_calibrate_stage_oof.py`): raw win で fit(two_gamma 非適用を assert)・prequential prior-only・atomic verdict(片 stage 悪化で REJECT)・NO_DECISION(held-out 不足)・**リークガード**(held-out 年 Y の結果変更で Y に適用する λ 不変・Y metrics と後続 fold のみ変化)。

## Phase 2 — US2: deployment final-fit + manifest 生成 (P1)

- [X] T006 prequential-eval と deployment-final-fit の分離(`oof_calibration.py`): verdict 決定後、**全 eligible OOF sample で λ/γ を再 fit**(D2)。manifest 用 params = final-fit or policy-selected identity のみ。rejected candidate の fitted params は evaluation に保存。two_gamma 側(`calibrate_oof`)も同型に final-fit を追加。
- [ ] T007 frozen calibration-sample + result-snapshot artifact(`training/src/horseracing_training/` 新 module): calibration_sample_hash・result_snapshot_hash・reference checksum を content-addressed 保存(D7)。決定論=明示 sort((race_date,race_id)/horse_id)・NaN/Inf 再帰拒否。
- [X] T008 manifest **v3**(`probability/src/horseracing_probability/calib_manifest.py`): `SCHEMA_VERSION=3`・構造化 evaluation(両 stage verdict+params+fit_through/fit_race_set_hash/n_fit)・versioned eligibility policy・**consumer 別 pipeline**(serving_raw / betting post-two-gamma)。`verify_manifest` が eligibility を**再計算**(nonidentity⟺ADOPT・REJECT⟹identity・stage set/order・pivot/探索範囲整合・gate-config hash・policy version)(D9)。fit_through=max deployment-fit 日(D5)。
- [X] T009 [P] manifest v3 verify 単体テスト(`training/tests/unit/test_calib_manifest_v3.py`): v3 受理・v2 は活性不可(移行 or 拒否方針を明記)・eligibility 再計算(nonidentity+REJECT で拒否・identity+ADOPT で拒否)・verdict matrix(D6 の 6 行)。
- [X] T010 manifest path を manifest_digest-keyed に(`calib_manifest.py::manifest_path`): `artifacts/oof/<bundle>/manifests/<manifest_digest>/manifest.json`(D7 の bundle_digest 固定 conflict 是正)・create-only atomic。
- [X] T011 `generate-manifest` CLI(`training/src/horseracing_training/cli.py`): bundle load(`--bundle` or `--generate-bundle`)→ calibrate_oof + calibrate_stage_oof → deployment refit → attestation → build_manifest(v3)→ write(digest-keyed)。dirty tree / `code_sha=unknown` は production で拒否(D7)。stdout に verdict・fit_through・digest・path。
- [X] T012 [P] `generate-manifest` 統合テスト(`training/tests/integration/test_generate_manifest.py`): fixture bundle → 生成 manifest が **076 loader で activate 可**(scope=production・eligible=verdict)・**2-process byte 一致**(決定論・D7)・同 bundle 別評価が別 manifest_digest で共存(append-only)。

## Phase 3 — US3: full OOF job + 検証 (P2・operator)

- [X] T013 OOF-replay parity 検証(`training/tests/integration/test_oof_replay_parity.py` + CLI `verify-manifest-parity`): OOF win vector を production 純 apply(engine two_gamma / serving stage）へ replay → per-horse top2/top3 完全一致・**win byte 不変**・Σ≈2/3・単調性・identity byte parity(D8)。production persisted predictions で「改善」再評価しない。
- [X] T014 別 promotion record(append-only・`training/`): candidate manifest → promoted の分離記録(どの manifest を production で有効化したか)。DB schema 不変(disk artifact)。
- [~] T015 実 DB full OOF job(operator・手動): 実 lgbm-063 で `generate_oof_bundle`(数時間)→ `generate-manifest` → THE production manifest。結果(γ/λ・verdict・fit_through・digest)を plan/summary に記録。
- [ ] T016 実 γ/λ で 076 gate 再走(operator): full-precision parity・digest token・runtime fit 非参照・全 entry path・fail-closed。実 manifest で win byte-parity 再確認。
- [ ] T017 [P] ruff / 全パッケージ回帰(activation OFF 既定で緑・FR-015 相当=生成は既存 serving/betting/api を変えない)。memory/CLAUDE 整合更新・deploy README の do-not-default-ON waiver を「実 manifest 検証後に解除可」に更新。

**注記**: Phase 1 は自己完結(fixture テスト可)。Phase 2 が manifest v3 で最大。Phase 3 は operator の長時間ジョブ。
prospective shadow(post-activation confirmatory)は 065 基盤で運用蓄積後=078 スコープ外。
