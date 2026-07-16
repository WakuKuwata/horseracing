# Implementation Plan: OOF-faithful Calibration Evidence

**Branch**: `074-oof-faithful-calibration` | **Date**: 2026-07-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/074-oof-faithful-calibration/spec.md`

## Summary

073 codex レビューで判明した校正リーク(`_latest_run_predictions` が base_model_version 非限定 + 過去 prediction は full-history=非OOS)を是正する第一段。**fold ごと strict-past 再学習した recipe-faithful モデルの OOF prediction**(content-addressed disk artifact)を作り、その上で two-gamma(048)/ stage λ(049)を prior OOF のみで prequential fit・strictly-later OOF block で ECE 評価・048 採否を OOF で測り直す。最小 content-addressed manifest で束ねる。**production 結線なし**(activation=076、global registry=077)。

**再利用の核**: `eval/foldfit.predict_over_folds(factory, eval_races)` が既に OOF(expanding fold ごと outer-train で再 fit → valid 予測・保存 booster 不使用=codex C1)。074 の OOF bundle は、**lgbm-063 の legacy attestation から構築した recipe-faithful RecipeFactory** をこれに通して得た per-race prediction を content-addressed disk artifact に直列化するだけ。校正 fit は既存 `fit_two_gamma`/`fit_product_stage_discount` の **sample source を `load_p_samples`(leaky)から OOF bundle に差し替える**。

**codex 設計レビュー**: `docs/plan/codex-074-review.md`(2026-07-16、実コード読解)。全採用。採否は下記 Constitution Check 直後に記録。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: lightgbm / numpy / pandas / scikit-learn(既存)、SQLAlchemy 2.0(read-only)。新規依存なし。

**Storage**: PostgreSQL 16(**読み取りのみ・migration なし**)。OOF bundle・legacy attestation・calibration evaluation artifact・manifest はすべて content-addressed disk artifact(`artifacts/` 配下、prediction_runs 非保存=API/serving/model-selector 非汚染)。

**Testing**: pytest + testcontainers。OOF strict-past・leak-guard・byte 決定論・manifest fail-closed。

**Target Platform**: オフライン research CLI。**production(serving/betting/API)には結線しない**。

**Project Type**: 既存マルチパッケージ。触るのは主に `probability/`(校正 sample source)+ `training/`(legacy attestation・OOF bundle 生成 CLI)+ `eval/`(既存 foldfit/paired 再利用)。

**Performance Goals**: N/A(evidence が目的)。**計算コスト高**: fold ごと再学習=pl_topk フル walk-forward で十数時間級(前例)。長時間 job 前提。決定論許容誤差 gate-config 事前登録。

**Constraints**: lgbm-063 の persisted **win** バイト不変(SC-006)。schema-zero。073 過去 verdict 不変。production 挙動不変(FR-015)。

**Scale/Scope**: 2008–2026 development evidence(confirmatory でない)。触るコード: `probability/model_calibration.py`(sample source・base_mv フィルタ)、新 `training` OOF-bundle 生成 + legacy attestation + manifest モジュール、`eval/foldfit`/`paired` 再利用。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: raceId/ラベル契約不変。ID/ラベル定義に触れない。**PASS**。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 074 の本体がリーク是正。OOF は strict-past(`max(train_date)<race_date`)・同日除外を `race_date<target_date` に統一・対象結果変更で OOF 不変(leak-guard test)。校正派生値(γ/λ/ECE/verdict)をモデル特徴に還流しない(FR-018)。**PASS(強化)**。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: OOF prequential fit + strictly-later OOF block 評価。048 採否を OOF で測り直し(ADOPT/REJECT/NO_DECISION)。事前登録 gate-config。073 過去 verdict 不変。2008–2026 は development evidence(confirmatory でない)。**PASS**。
- [x] **IV. 確率整合性**: 校正後も Σ 整合・順位保存(stage discount は top2/top3、Σ≈2/3)。win 不変。**PASS**。
- [x] **V. 再現性・監査**: content-addressed manifest(full 精度 γ/λ・fold race hash・OOF checksum・code SHA)・append-only evaluation artifact・create-only・atomic publish・fail-closed。**PASS(強化)**。
- [x] **VI. feature 分割規律**: スキーマ変更ゼロ・migration なし。production/API に触れない(activation=076・realized 改名=075・registry=077 に分離)。1 feature 1 関心(evidence)。**PASS**。
- [x] **品質ゲート**: codex second opinion 取得済み(`docs/plan/codex-074-review.md`)。採否を下記に記録。**PASS**。

### codex second opinion 記録(差分と採否)

| codex 指摘 | 採否 | 反映 |
|---|---|---|
| persisted-run 再利用は不可(過去 prediction=full-history=非OOS)。OOF は fold 再学習の content-addressed disk artifact(prediction_runs 非保存) | **採用** | US1 / FR-001/005/017 / research D1 |
| `_latest_run_predictions(base_model_version)` 修正は defense-in-depth だが OOF 正本には使わない・latest でなく manifest 固定 run_id | **採用** | research D1(bundle は foldfit 由来・DB run は読まない) |
| 現 ModelRecipe は完全再現 recipe でない→legacy attestation 必須 | **採用** | US2 / FR-007/008 / research D2 |
| serving parity 線引き(win byte 不変・表示 top2/top3 は新 run で可・既存 run 不変) | **採用** | FR-015/016 / SC-006/SC-010 / research D3 |
| manifest は 3-file freeze 拡張では不足(metadata checksum・full 精度 γ/λ・fold hash 等) | **採用** | US4 / FR-013/014 / research D4 |
| 3分割(074 evidence / 076 activation / 077 registry)・最小 OOF manifest は 074 に残す | **採用** | スコープ節・spec 依存節 |
| 073 FR-007 は 074 の append-only artifact への参照で fulfill・過去 verdict 不変 | **採用** | US3 / FR-012 / research D5 |
| 同日リーク: `race_date<target_date` に統一(race_id 順で同日 earlier を使わない) | **採用** | FR-003 / research D6 |
| 048 verdict を OOF で測り直す(ADOPT/REJECT/NO_DECISION 許容) | **採用** | FR-011 / research D5 |
| 066 dispersion・joint calibration にも leaky loader | **採用(診断)** | research D7(074 は evidence のみ・是正結線は 076) |
| OOF→full-history 分布 transfer check・NO_DECISION/fallback | **採用** | FR-011 / research D5 |
| feature hash は列名中心=値意味論変更に弱い | **採用** | source fingerprint で補完(research D4) |

**保留・不採用**: なし。全採用または後続 feature(076/077)へ分離。

## Project Structure

### Documentation (this feature)

```text
specs/074-oof-faithful-calibration/
├── plan.md · research.md · data-model.md · quickstart.md
├── gate-config.json     # 048 OOF 再検証の事前登録ゲート(OOS 前固定, III)
├── contracts/cli.md     # OOF-bundle 生成 / calibrate-oof / manifest 検証 CLI
└── tasks.md             # /speckit-tasks で生成
```

### Source Code (repository root)

```text
probability/src/horseracing_probability/
├── model_calibration.py   # 校正 sample source を OOF bundle に差し替え・_latest_run_predictions に base_mv フィルタ(defense-in-depth)
└── oof_bundle.py (新)     # OOF prediction bundle の読み書き(content-addressed)

training/src/horseracing_training/
├── legacy_attest.py (新)  # lgbm-063 の完全 resolved recipe attestation(073 freeze を起点)
├── oof_generate.py (新)   # foldfit.predict_over_folds を recipe-faithful factory で回し OOF bundle 直列化
├── calib_manifest.py (新) # content-addressed manifest(create-only/atomic/fail-closed)
└── cli.py                 # oof-generate / calibrate-oof / verify-manifest サブコマンド

eval/src/horseracing_eval/
├── foldfit.py             # 既存 predict_over_folds を OOF 生成に再利用(変更なし想定)
└── (calibrated-stage ECE は paired/harness の帯別 ECE を strictly-later OOF block に適用)

artifacts/oof/<digest>/    # OOF bundle・manifest・evaluation artifact(gitignore・DB から決定論再生成)

*/tests/                   # OOF strict-past・同日除外・結果変更で不変・bundle digest 安定・
                           # 決定論・win byte parity・manifest fail-closed・冪等
```

**Structure Decision**: `probability/`(校正 sample source)+ `training/`(attestation・OOF 生成・manifest)に閉じる。`eval/foldfit` は既存を再利用。**serving/betting/api/db/front には触れない**(activation は 076)。

## Complexity Tracking

| 論点 | 判断 |
|---|---|
| OOF を一から実装するか | **しない**。`foldfit.predict_over_folds` が既に OOF(codex C1)。recipe-faithful factory を通すだけ |
| ModelRecipe を拡張して attestation を持たせるか | 073 で `calibration_split_unit` を足した ModelRecipe を土台に、legacy_attest.py で **不足フィールド(resolved params/ordered cols/HPO 等)を metadata.json から補完**した完全 attestation を別 artifact 化(recipe_hash 破壊を避ける) |
| 計算コスト(fold 再学習=長時間) | tasks は「長時間 job」を明示し、smoke(小 fold)→フル(operator)に段階化。実装可否ゲートは smoke で判定 |
| stage discount の win 不変 | 049 の設計(top2/top3 のみ校正・win 非適用)を維持。OOF 化しても win は触らない(SC-006) |
