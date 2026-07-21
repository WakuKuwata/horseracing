# Data Model: Probability Pipeline Activation (076)

**スキーマ変更ゼロ・migration なし**。DB エンティティは変更しない。本 feature の「モデル」は
disk artifact(manifest v2)と in-memory 値(Activation)、および `logic_version` token 契約。

## 1. calibration manifest — schema v2(disk artifact, content-addressed)

074 の schema v1 を **additive 拡張**。既存フィールドは不変、2 フィールド追加、`schema_version` を
`1→2`(旧 manifest は fail-closed=明示非対応)。

| フィールド | v1 | 型 | 意味 |
|---|---|---|---|
| `schema_version` | ✓(=1) | int | **v2 は `2`**。loader は既知世代のみ受理 |
| `artifact_kind` | ✓ | str | `"oof_calibration"` |
| `base_model_version` | ✓ | str | `"lgbm-063"`(世代束縛) |
| `attestation_digest` | ✓ | sha256 | legacy recipe attestation の `stable_hash(full payload)`。loader は `attestation_from_model_dir(dir, code_sha)` で**再計算して照合**(4-key checksum では再構成不能=C1) |
| `bundle_digest` | ✓ | sha256 | OOF prediction bundle |
| `evaluation` | ✓ | obj | ECE/verdict 等 |
| `checksums` | ✓ | obj | `{attestation, bundle, evaluation}` sha256 |
| `probability_stage_order` | ✓ | list | 確率 stage 適用順 |
| `full_precision_params.two_gamma` | ✓ | obj | **`{gamma_lo, gamma_hi, pivot}`**(丸めない) |
| `full_precision_params.stage_lambdas` | ✓ | obj | **`{top2, top3}`**(丸めない) |
| `code_sha` / `seed` / `num_threads` | ✓ | — | 決定論 provenance |
| `manifest_digest` | ✓ | sha256 | payload 全体(wall-clock 除く)の digest |
| **`artifact_scope`** | ✗ **新** | str | `fixture` \| `production`(2 値・`smoke` は 076 では未使用=不採用) |
| **`activation_eligible`** | ✗ **新** | bool | production 経路で activate 可か |
| **`fit_through`** | ✗ **新** | date | **top-level フィールド**(loader/T003 が読む単一の場所)。校正 fit の最終日(時間検証・D8) |

**検証(`verify_manifest` v2, fail-closed=apply 前に例外)**:
- `schema_version==2` / `artifact_kind` 既知 / `base_model_version` 一致(世代不一致=reject)
- 必須フィールド全存在(partial=reject)/ sha256 形式 / `manifest_digest` 再計算一致(改竄=reject)
- `two_gamma` に `{gamma_lo,gamma_hi,pivot}` 全存在 / `stage_lambdas` 非空 mapping
- **新**: `artifact_scope` ∈ 既定集合 / `activation_eligible` は bool

**不変条件**: 同 payload=同 `manifest_digest`(冪等)。create-only・atomic publish(074 既存)。
disk artifact は read-only 運用(D9)。**prediction_runs に入れない**(API/serving/model-selector 非汚染)。

## 2. Activation — in-memory(loader 戻り値)

```
Activation:
  two_gamma:        PCalibrator      # method="two_gamma", params={gamma_lo,gamma_hi,pivot}
  stage_discount:   StageDiscount    # lambda2=stage_lambdas.top2, lambda3=stage_lambdas.top3
  manifest_digest:  str              # 監査/冪等キー用の完全 digest(token は [:12])
  mode:             str              # "manifest-required"(activate 済み)
  fit_through:      date             # per-day 時間判定用(backfill は load-once 後この値で日毎に判定)

  # per-target_date 時間チェック(load-once と両立=ファイル再読込しない・FR-018×FR-021)
  applies_to(target_date) -> bool    # target_date > fit_through なら True
  assert_applies(target_date)        # False なら ActivationError(backfill の per-day fail-closed に使う)
```

**マッピング規律(回帰テストで固定)**: `stage_lambdas.top2 → StageDiscount.lambda2` /
`stage_lambdas.top3 → StageDiscount.lambda3`(キー取り違え禁止=[[feature-074-manifest-unwired]])。
manifest には provenance 系フィールド(`n_races_l2/n_races_l3/fallback`)が無いため manifest 由来の
`StageDiscount` は既定(`n_races=0`/`fallback=False`)で構成する=λ は既に fit 済みなので apply 挙動に
影響しない。identity 校正(under-sampled 由来)も有効な Activation として扱う(apply は no-op)。

## 3. ActivationMode / Profile — enum

| enum | 値 | 意味 |
|---|---|---|
| `ActivationMode` | `legacy-runtime` | 既定。現行 runtime fit(`load_p_samples`+fit)・バイト同等 |
| | `manifest-required` | manifest 必須。無効/欠如=致命的・**runtime fit に fallback しない** |
| `Profile` | `production` | fixture/未 eligible を拒否 |
| | `fixture` | テスト用。fixture manifest を受理 |

## 4. logic_version token 契約

manifest 由来出力は既存 token に追記(順序は既存規律に従う):

```
…;calib=<manifest_digest[:12]>;calibmode=manifest
```

- **betting**: 既存 `;pcal=…`(two_gamma params)に加え `;calib=<digest>` を付す(runtime-fit 由来と区別)。
- **serving**: 既存 `;sdisc=harville;l2=…;l3=…` に加え `;calib=<digest>`。
- **dispersion pcal**: artifact の `version`/logic に `;calib=<digest>`。
- **冪等キー**: serving backfill(044)/ betting recommend(043/045)の「既存 run あり?」判定に
  `;calib=<digest>` を含める → 別 digest は別 run(silent skip 禁止・FR-010)。advisory lock で並行重複防止。

## 5. Allowed-change matrix(parity ゲートの正本)

| 出力面 | activation 後の契約 | parity テスト |
|---|---|---|
| `race_predictions.win_prob` / API `horses[].win` | **byte-identical** | `model_internal_win_parity` (SC-001) |
| persisted/API `top2`/`top3` | 新 λ で新規 run の値は**変更可**(既存行不変) | `display_topk_parity` (SC-003) |
| win recommendation(選択・pseudo odds/ROI・Kelly stake) | two-gamma 変更で**変更可** | `betting_two_gamma_parity` (SC-002) |
| exotic recommendation joint | **λ=1 構造維持**・p は two-gamma で**変わる**(OFF と byte 不一致) | `betting_joint_identity_stage_parity` (SC-004b) |
| API `?bet_type=` joint | **λ=1・byte-identical**(不変 win_prob から再計算) | `api_joint_legacy_parity` (SC-004a) |
| `race_dispersion.model_delta` | manifest 由来で**変更可**(band は q のみ=不変) | dispersion 直読テスト (US3) |
| `race_dispersion.band` / raw q | **不変**(q のみの関数) | band 不変テスト |

## 6. leak-guard 対象(特徴量に還流させない)

`manifest_digest` / `γ_lo,γ_hi,pivot` / `stage_lambdas` / Activation の値は、**特徴量列・feature_hash・
FEATURE_VERSION に一切入らない**(token grep + import-graph + behavioral: activated 経路が
`load_p_samples`/`_latest_run_predictions`/任意 fit/`RaceResult` クエリを呼ばない=SC-009)。
