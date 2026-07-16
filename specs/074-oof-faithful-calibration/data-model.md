# Data Model: OOF-faithful Calibration Evidence

**Feature**: 074 | **スキーマ変更**: なし。すべて content-addressed disk artifact(`artifacts/oof/<digest>/`、gitignore・DB から決定論再生成)。

## 1. legacy recipe attestation(training/legacy_attest.py)

lgbm-063 の完全 resolved recipe。073 freeze + metadata.json 由来。

| フィールド | 説明 |
|---|---|
| `base_model_version` | `lgbm-063` |
| `resolved_lgbm_params` | 解決済み booster params(num_leaves/min_child_samples/reg_lambda/feature_fraction/learning_rate/rounds 等) |
| `objective` / `postprocess` | `pl_topk` / `group_softmax` |
| `ordered_feature_columns` / `feature_version` | 順序付き列 + `features-017` |
| `target_encode_cols` / `te_smoothing` | TE 列・smoothing |
| `internal_calibration` | method / calib_frac / `calibration_split_unit=race_count_v1`(073) |
| `seed` / `num_threads` | 決定論条件 |
| `drop_features` | drop list |
| `source_fingerprint` / `materialized_hash` | source 列意味論 hash(値意味論弱点補完) |
| `code_sha` | 生成時 code SHA |
| `attestation_digest` | 上記 canonical payload の SHA-256 |

**検証**: フィールド欠落/差異で OOF 再構築が fail-closed(または新 digest)。

## 2. OOF prediction bundle(probability/oof_bundle.py)

| フィールド | 説明 |
|---|---|
| `predictions` | `{race_id: {horse_id: win/top2/top3}}`(foldfit 由来) |
| `fold_boundaries` | expanding fold の valid year 列 |
| `per_fold` | fold ごと train/valid race set hash・train_through・生成 model digest |
| `oof_race_set_hash` | OOF race 集合 hash |
| `prediction_checksum` | prediction 値の checksum |
| `attestation_digest` | 生成に使った legacy attestation の digest |
| `bundle_digest` | 上記 canonical payload の SHA-256(persisted-run 非依存=FR-005) |

**不変条件**: strict-past(全 race `max(train_date)<race_date`)・同日除外(`race_date<target_date`)・対象結果変更で不変(result hash のみ変化)・byte 決定論。

## 3. calibration evaluation artifact(append-only)

| フィールド | 説明 |
|---|---|
| `evaluation_contract_version` | `v2`(073 と同契約) |
| `stage` | `two_gamma_win` / `stage_discount_top2` / `stage_discount_top3` |
| `fit` | prior-OOF prequential fit の γ_lo/γ_hi/λ(**full 精度**)・**pivot=0.15(048 事前登録の固定値を維持・再 fit しない)**・fit race hash・num_threads fallback 注記(research D8) |
| `ece` | strictly-later OOF block の calibrated-stage ECE(帯別) |
| `verdict` | ADOPT / REJECT / NO_DECISION(048 再検証・点推定不可) |
| `transfer_check` | OOF→full-history 分布ミスマッチ判定(ミスマッチ=NO_DECISION/fallback) |
| `gate_config_hash` | 事前登録 gate-config の hash |
| `bundle_digest` / `attestation_digest` | 参照する OOF bundle / attestation |

**参照**: 073 FR-007 はこの artifact を参照して fulfill。073 過去 verdict/result は不変。

## 4. content-addressed manifest(training/calib_manifest.py)

上記 1–3 を byte 再現可能に束ねる。FR-013 の完全情報(schema/version・checksum 群・full 精度 γ/λ・fold hash・stage 順・code SHA/seed/threads・最終出力 checksum)。

**状態遷移**: create-only(temp→atomic rename)。同 payload=同 digest(冪等成功)/同 key 異内容=conflict/改竄・partial・未知 schema・世代不一致=load 前拒否。wall-clock/自己 digest は hash 対象外。identity fallback も明示 artifact。

## Key entity 間の関係

- legacy attestation → RecipeFactory → OOF bundle(bundle は attestation_digest を参照)
- OOF bundle → calibration evaluation(fit/ECE/verdict は bundle_digest を参照)
- manifest = attestation + bundle + evaluation を content-addressed に統合
- **production(serving/betting/API)はどれも読まない**(FR-015、activation は 076)
