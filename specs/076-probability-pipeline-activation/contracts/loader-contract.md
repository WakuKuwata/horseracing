# Contract: `probability/calib_activation.py::load_calibration`

唯一の manifest 解釈点。3 経路(betting/serving/dispersion)はこれのみを経由する(FR-006)。

## Signature

```python
def load_calibration(
    manifest_path: str | os.PathLike[str],   # 絶対パス / immutable URI(相対禁止・D5)
    *,
    active_model_version: str,                # 照合対象(選択 run の model・FR-020)
    target_date: datetime.date | None = None, # 単一レースの時間検証(D8)。backfill は None→per-day
    profile: Profile = Profile.PRODUCTION,
    attestation_verifier: Callable[[dict], None] | None = None,  # strong binding 注入(D11)
) -> Activation:
    ...
```

**D11(実装時是正)**: 当初案の `active_model_dir` 引数で loader 内 attestation 再計算 → **probability→
training 循環**で不成立。代わりに **`attestation_verifier` 注入**。training を持つ経路(serving/live/
dispersion-pcal)は `training.calib_binding.model_dir_attestation_verifier(model_dir)` を渡して strong
binding(FR-019 完全)。betting/api は `None`(name+content-address binding、strong 全経路化は 077)。

戻り値 `Activation` は data-model.md §2。1 invocation につき 1 回呼ぶ(全レース同一 digest・D5)。

**`active_model_dir` の解決規則(単一・全パッケージ共通)**: 対象 run/serving model の
`model_versions.weights_uri` の親ディレクトリ(`Path(weights_uri).parent`=metadata.json/attestation が
在る dir)を **絶対パス化**して渡す([[weights-uri-relative-path-ops-bug]] の前例=相対 `weights_uri` は
ops subprocess(cwd=serving)から壊れるため、呼出側で必ず絶対化)。betting/serving/dispersion で同一規則。
loader 内でも絶対パスでなければ `ActivationError`(相対禁止)。

## 動作(順序固定・全て apply 前)

0. **パス検証**: `manifest_path` が絶対パスでなければ `ActivationError`(相対禁止=D5)。**全 entry path は
   この loader 内の検証を継承**(各 CLI で個別実装しない=F4)。**load-once の分離**: manifest の**ファイル
   I/O + `verify_manifest` + 世代/scope 照合は 1 invocation 1 回**(全レース同一 digest)。**時間照合
   (`target_date <= fit_through`)だけは per-target_date**(backfill は per-day)で評価する=読み込みを
   繰り返さず日ごとに時間判定のみ行う(FR-018 と FR-021 の両立)。実装は「一度ロードした検証済み manifest
   を保持し、`target_date` を引数に取る軽量な時間チェック関数」を分離する。
1. `verify_manifest(manifest_path)`(v2)— schema/世代/checksum/manifest_digest/param 形状。失敗=`ManifestError`。
2. **世代照合**: `manifest.base_model_version == active_model_version`。不一致=`ActivationError`。
   **加えて attestation 再計算照合**: 074 の
   [`attestation_from_model_dir(active_model_dir, code_sha=manifest.code_sha)`](../../../training/src/horseracing_training/legacy_attest.py)
   で **resolved model dir から attestation payload を再構築して `attestation_digest`(=`stable_hash(payload)`)
   を計算**し、`manifest.attestation_digest` と一致比較する(D3・FR-019=`save_model_version` 上書き耐性)。
   4-key checksum mapping では 074 の full-payload digest を再構成できないため、**model dir + code_sha**
   を渡して 074 の実関数で再計算するのが正しい照合機構。不一致=`ActivationError`。
3. **scope 照合**: `profile=="production"` のとき `artifact_scope=="production"` かつ `activation_eligible`。
   fixture/未 eligible=`ActivationError`(FR-016)。
4. **時間照合**: `target_date > manifest.fit_through`。違反(`<=`)=`ActivationError`(D8)。
   **単一レース経路**は `load_calibration` に `target_date` を渡してここで判定。**backfill 経路**は
   `load_calibration` を **1 回**呼んで検証済み `Activation` を得たのち、日毎に `Activation.assert_applies
   (day)`(ファイル再読込なし=load-once×per-day 両立・FR-018×FR-021)を呼ぶ。
5. **マッピング**: `two_gamma → PCalibrator(method="two_gamma", params=…)`,
   `stage_lambdas{top2,top3} → StageDiscount(lambda2=top2, lambda3=top3)`。
6. `Activation(two_gamma, stage_discount, manifest_digest, mode="manifest-required", fit_through)` を返す。

## Fail-closed 契約(SC-005 / SC-010 / SC-012)

| 事象 | 結果 |
|---|---|
| ファイル欠如/読めない/JSON 不正 | `ManifestError` |
| 改竄(manifest_digest 不一致) | `ManifestError` |
| partial(必須欠落) / 未知 `schema_version` | `ManifestError` |
| 世代不一致(base model 名 / 再計算 `attestation_digest` 不一致) | `ActivationError` |
| scope=fixture/未 eligible を production profile で | `ActivationError` |
| `target_date <= fit_through` | `ActivationError` |

**いずれも runtime fit へ黙って fallback しない**。呼び出し側(mode=`manifest-required`)は例外を伝播し、
CLI は非 0 終了・0 行書込(FR-022)。backfill は**ループ(per-day/per-race 例外隔離)の前に 1 回検証**。

## 呼び出し側の注入(既存 apply 経路・新校正ロジックなし)

| 経路 | 注入 |
|---|---|
| betting | `generate_recommendations(session, …, p_calibrator=act.two_gamma)`(046 既存 param) |
| serving | `run_serving(session, …, stage_discount=act.stage_discount)`(`_fit_stage_discount` 差替) |
| dispersion | `_build_model_delta(pmap, entropy, calibrator=act.two_gamma)`(直読・派生 JSON 廃止) |

## Mode

- `legacy-runtime`(既定): `load_calibration` を呼ばず現行 runtime fit。出力バイト同等(FR-004/SC-007)。
- `manifest-required`: 上記フロー。例外は致命的。
