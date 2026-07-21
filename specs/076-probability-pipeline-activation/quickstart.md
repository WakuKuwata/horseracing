# Quickstart: Probability Pipeline Activation (076)

fixture manifest による E2E 検証手順。**実 manifest 生成は follow-up**(ここでは `build_manifest` で
組んだ fixture を使う)。前提: 実 DB(lgbm-063 active)+ 076 実装済み。

## 0. 前提

- active model = `lgbm-063`(074/076 の parity oracle)。
- `probability/calib_activation.py` 実装済み・`calib_manifest.py` v2 拡張済み。

## 1. fixture manifest を作る

`build_manifest(..., artifact_scope="fixture", activation_eligible=False)` で fixture を、
`artifact_scope="production", activation_eligible=True` で production-eligible fixture を artifacts 配下に
書く(絶対パス)。両者の `full_precision_params` は既知の γ/λ を入れる(検証で param 一致を assert)。

## 2. win byte-parity(SC-001)— 最重要

同一レースを (a) `--calib-mode legacy-runtime`(既定)と (b) `--calib-manifest <prod-fixture> --calib-mode
manifest-required` で serving し、`race_predictions.win_prob` が **16 頭 mismatch 0**(byte-identical)。
top2/top3 は (b) で fixture の λ に変わる(`display_topk_parity`)。

## 3. betting two-gamma activation(US1・SC-002）

`recommend --race-id … --calib-manifest <prod-fixture> --calib-mode manifest-required` →
生成推薦の `logic_version` に `;calib=<digest12>;calibmode=manifest`。同 manifest の two-gamma params が
evaluator と一致。manifest 無しの `recommend` は現行とバイト同等(SC-007)。

## 4. fail-closed(SC-005/010/012)

- fixture manifest(`artifact_scope=fixture`)を `--calib-mode manifest-required` + production profile で
  → **非 0 終了・0 行**(fixture 拒否)。
- 改竄 manifest(`manifest_digest` を書換)→ `ManifestError` で非 0。
- `target_date <= fit_through` の manifest → `ActivationError` で非 0。
- いずれも **runtime fit に fallback しない**(ログに fallback 痕跡が無いこと)。

## 5. 冪等(SC-006）

`predict-backfill --from D --to D --calib-manifest <mA>` → 生成。同 `mA` 再実行=skip。
別 manifest `mB` で再実行=**別 run 生成**(top2/top3 更新・既存 run 不変)。

## 6. joint λ=1(SC-004a/b)

activation ON でも `GET /api/v1/…?bet_type=exacta&top=K` の API joint は **λ=1・activation OFF とバイト
一致**(SC-004a、不変 win_prob 由来)。exotic betting EV は **λ=1 構造は維持するが p は two-gamma で変わる**
(SC-004b、OFF と byte 不一致)。

## 7. dispersion 直読(US3・D10)

`--calib-manifest` 指定の dispersion 読み出しで `model_delta` が manifest 由来 two-gamma を使う
(派生 pcal JSON を生成しない)。band / raw q は不変。API が candidate run を選択した場合、model_delta は
**その run の model** と照合(FR-020)。

## 8. leak-guard(SC-009)

`manifest-required` 経路のトレース/import-graph で `load_p_samples` / `_latest_run_predictions` / 任意の
fit 関数 / `RaceResult` クエリの呼び出しが **0**。

## 9. 全 entry path(SC-011)

CLI / `live refresh` / `live collect-prospective` / ops subprocess で同一 fixture を指定 → 全て同一
`manifest_digest` を解決し、出力 logic_version の `;calib=` が一致。

---

## T029 実 DB E2E スモーク結果(2026-07-21 実施・horseracing DB / lgbm-063 active)

対象レース `202610020612`(2026-07-12・started 17 頭・odds 完備)。fixture manifest 使用(実 manifest 生成は follow-up)。

| 手順 | 検証 | 結果 |
|---|---|---|
| 2 win byte-parity(SC-001) | legacy run vs manifest run の win_prob 照合 | **17 頭 mismatch 0**・top3 は 17 頭全変化(manifest λ=0.82/0.70 が表示のみ変更) ✅ |
| 3 betting two-gamma(US1・SC-002) | recommend-serve manifest → lv | `pcal=two_gamma;gamma_lo=1.60000;gamma_hi=0.50000;pivot=0.15;calib=9e742906c1d4;calibmode=manifest;base_mv=lgbm-063`・全券種に `;calib=` ✅ |
| 4 fail-closed(SC-005/010/012) | fixture-scope=exit1(not production-eligible)・改竄=ManifestError(digest mismatch)・時間=ActivationError(within fit window)・相対パス=exit2・**P0-1 混在拒否**=different calibration mode refuse。**失敗試行は 0 行書き込み** ✅ |
| 5 冪等(SC-006) | 同一 manifest 再実行 | SKIPPED・recs 29→29 変化なし ✅ |
| 8 leak-guard(SC-009) | manifest 経路の leaky loader 参照 | calib_activation.py 内 `load_p_samples`/`_latest_run_predictions` = 0 参照 ✅ |

手順 6(joint λ=1)/7(dispersion 直読)/9(全 entry path)は自動統合テストで緑(test_joint_legacy_parity・test_dispersion_calib_activation・test_entry_path_calib・test_ops_calib_argv)=API サーバ常駐不要のため自動化側で担保。

**限界**: fixture manifest(合成 attestation)による WIRING 検証。実 γ/λ 値の妥当性は実 manifest 生成(stage-λ OOF fit + build_manifest 結線=blocking follow-up)まで未検証。
**スモーク副産物**: 対象レースに prediction_run 2 件(d491c80a legacy / 45cc36b3 manifest)+ recs 29 件(digest 9e742906）を append-only 生成。
