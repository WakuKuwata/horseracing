# CLI Contract: OOF-faithful Calibration Evidence

**Feature**: 074 | 対象: `training` CLI(OOF 生成・校正再検証・manifest 検証)。**production/API/serving には触れない**。read-only(DB 書込なし・disk artifact のみ)。

## training oof-generate

```
uv run --project training python -m horseracing_training.cli oof-generate \
  --base-model-version lgbm-063 \
  --from <YYYY-MM-DD> --to <YYYY-MM-DD> \
  --first-valid-year 2008 --seed <n> --num-threads 1 \
  --out artifacts/oof \
  [--smoke]        # 小 fold で実装可否ゲート(フルは長時間 job)
```
- lgbm-063 の legacy attestation を構築 → recipe-faithful RecipeFactory → `foldfit.predict_over_folds` で OOF prediction → content-addressed bundle を `artifacts/oof/<digest>/` に atomic publish。
- strict-past・同日除外(`race_date<target_date`)・byte 決定論。既存 bundle と同 payload は冪等成功。

## training calibrate-oof

```
uv run --project training python -m horseracing_training.cli calibrate-oof \
  --bundle artifacts/oof/<digest> \
  --gate-config specs/074-oof-faithful-calibration/gate-config.json \
  --stage {two_gamma_win,stage_discount_top2,stage_discount_top3,all} \
  --json <out>
```
- OOF bundle を sample source に two-gamma/λ を **prior OOF fold のみ prequential fit**、**strictly-later OOF block** で calibrated-stage ECE。
- 048 採否を OOF で測り直し verdict = ADOPT/REJECT/NO_DECISION。OOF→full-history transfer check(ミスマッチ=NO_DECISION/fallback)。
- `evaluation_contract_version=v2` の append-only evaluation artifact 出力。073 FR-007 参照 fulfill。

## training verify-manifest

```
uv run --project training python -m horseracing_training.cli verify-manifest \
  --manifest artifacts/oof/<digest>/manifest.json
```
- schema/version・checksum 群・full 精度 γ/λ・fold hash・stage 順・code SHA を検証。改竄/partial/未知 schema/世代不一致=拒否。同 payload=冪等成功・同 key 異内容=conflict。

## 破壊しない契約

- serving/betting/api/front/OpenAPI/DB schema 不変(FR-015)。
- lgbm-063 persisted win byte 不変(SC-006)。既存 PredictionRun/Recommendation 不変(SC-010)。
- 073 過去 verdict 不変(SC-009)。
- gate-config は OOS 結果を見た後に変更しない(III)。
