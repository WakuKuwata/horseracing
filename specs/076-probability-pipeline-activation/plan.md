# Implementation Plan: Probability Pipeline Activation & Parity

**Branch**: `076-probability-pipeline-activation` | **Date**: 2026-07-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/076-probability-pipeline-activation/spec.md`

## Summary

074 が作った immutable calibration manifest(`full_precision_params.{two_gamma, stage_lambdas}`・
content-addressed・`verify_manifest` で fail-closed 検証)を、production の 3 経路(betting 推薦の
two-gamma・serving 表示の stage discount・066 dispersion の model_delta)から**読む**ように結線する。
新規校正ロジックはゼロ=既存の runtime `fit` を、**manifest からのパラメータ読み込み + 既存 apply 経路**
に差し替えるだけ。安全は「明示 mode(`legacy-runtime`/`manifest-required`)+ 共有 fail-closed loader +
allowed-change matrix の parity ゲート」で担保する。

**技術核**: 単一の共有 loader `probability/calib_activation.py::load_calibration(manifest_path, *,
active_model_version, target_date, profile, active_model_dir)` が、074 の `verify_manifest` を
通し、世代(base model 名 + **`attestation_from_model_dir` で attestation を再計算し digest 照合**=
`save_model_version` 上書き耐性)・scope
(fixture/production)・時間(`target_date <= fit_through` 拒否)を検証して
`Activation{two_gamma: PCalibrator, stage_discount: StageDiscount, manifest_digest, mode}` を返す。
3 経路はこの Activation を **既存の apply 経路に注入**する:

- **betting**: `generate_recommendations(p_calibrator=activation.two_gamma)`(046 で既にある param)。
- **serving**: `run_serving(..., stage_discount=activation.stage_discount)`(`_fit_stage_discount` を差替)。
- **dispersion**: API が `activation.two_gamma` を**直接 consume**(派生 pcal JSON を廃止=codex)。

**win はバイト不変**(two-gamma は推薦時のみ・stage discount は top2/top3 のみ)。API/exotic joint は
λ=1 維持。activation は全 entry path(betting/serving CLI・`live/orchestrate`・`ops/runner` subprocess)
に結線する。**fixture-first**: 実装は fixture manifest で full 完成させ、実 manifest 生成(stage-λ OOF
fit + `build_manifest` 結線 + full OOF job)は blocking follow-up に分離する。

**codex 設計レビュー**: spec フェーズで codex(gpt-5.6-sol, xhigh, 実コード読解)に second opinion を取得
し**全指摘を採用**(spec 末尾 + research D0 に記録)。plan の設計判断は全て codex がすでに bless した
FR-004/016–022 の忠実な実装で、新規の非自明判断は manifest schema の additive v2 拡張(artifact_scope /
activation_eligible)のみ=codex FR-016 の直接実装。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: 既存のみ(lightgbm / numpy / pandas / SQLAlchemy 2.0)。**新規依存なし**。
074 の `training/calib_manifest.py`(`build_manifest`/`verify_manifest`)、`eval/stage_discount.py`
(`StageDiscount`)、`probability/model_calibration.py`(`PCalibrator`/`apply_p_calibrator`)を再利用。

**Storage**: PostgreSQL 16(読み取り + 既存の予測/推奨 append-only 書込のみ・**migration なし**)。
manifest は 074 の content-addressed disk artifact(`artifacts/` 配下、prediction_runs 非保存)。

**Testing**: pytest + testcontainers。parity(win byte / joint λ=1 / eval==serving)・leak-guard
(activated 経路が `load_p_samples`/`_latest_run_predictions`/fit/`RaceResult` を呼ばない)・fail-closed
(改竄/partial/世代/scope/時間)・冪等(digest key)・全 entry path 一致。

**Target Platform**: オフライン research/serving CLI + read-only API。

**Project Type**: 既存マルチパッケージ。触るのは `probability/`(新 loader)+ `betting/` + `serving/` +
`api/`(dispersion)+ `live/` + `ops/`(entry-path 結線)+ `training/`(manifest schema v2)+ `deploy/`
(README の read-only 運用ノート・docs のみ)。**db/front/admin/migration には触れない**。

**Performance Goals**: N/A(結線が目的)。manifest は 1 invocation 1 回ロード(全レース同一 digest)。

**Constraints**: lgbm-063 の persisted **win** バイト不変(SC-001)。schema-zero(DB)。API `?bet_type=`
joint λ=1 契約維持。既定 mode=`legacy-runtime`(現行挙動保存)。実 manifest 生成は out of scope。

**Scale/Scope**: fixture manifest による plumbing。触るコード ~7 パッケージの薄い結線 + 1 新 loader。

## Constitution Check

*GATE: Phase 0 前に PASS。Phase 1 後に再チェック。*

- [x] **I. データ契約**: raceId/ID 契約・ラベル名に変更なし(N/A に近い・PASS)。manifest の
  base_model_version は既存 model_version 契約に従う。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: **本 feature の主目的がリーク境界の是正**。activated 経路は
  `load_p_samples`(非OOS)を呼ばず OOF-faithful manifest を読む。校正派生値(γ/λ/digest)は特徴量に
  還流しない(token grep + behavioral leak-guard=FR-012/SC-009)。manifest の時間検証(`target_date <=
  fit_through` 拒否)で strict-past を担保。**PASS**。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: allowed-change matrix を parity ゲートとして**事前登録**
  (spec SC-001–012)。win byte / joint λ=1 / eval==serving を実装前に固定。値の「改善」ではなく「同一性
  /許容変更」の検証=採否ゲートでなく契約ゲート。walk-forward 学習は不要(校正値の読み替えのみ)。**PASS**。
- [x] **IV. 確率整合性**: win Σ=1 不変(触らない)。stage discount 後 top2/top3 は 049 の Σ≈2/3・単調・
  順位保存を維持(既存 apply 経路をそのまま使う)。**PASS**。
- [x] **V. 再現性と監査**: manifest digest を `logic_version` に token 化(`;calib=<digest12>`)+ 冪等
  キーに含める(FR-009/010)。既存 run/recommendation は append-only(書換なし)。**PASS**。
- [x] **VI. feature 分割規律**: スキーマ変更ゼロ・migration なし・API `?bet_type=` joint 契約不変・
  read-only 境界維持。manifest schema は disk artifact の additive v2(DB 非関与)。実 manifest 生成 /
  registry 化 / 既定 ON 昇格は後続に分離。**PASS**。
- [x] **品質ゲート**: codex second opinion をspec フェーズで取得・全指摘採用(research D0 に両案差分と
  採否を記録)。plan は codex bless 済み判断の忠実実装。**PASS**。

**ゲート結果: 全 PASS**(違反なし → Complexity Tracking は空)。

## Project Structure

### Documentation (this feature)

```text
specs/076-probability-pipeline-activation/
├── spec.md              # 完了
├── plan.md              # 本ファイル
├── research.md          # Phase 0(設計判断 D0–D10)
├── data-model.md        # Phase 1(manifest v2 / Activation / logic_version token)
├── contracts/           # Phase 1(loader-contract.md / cli-contract.md)
├── quickstart.md        # Phase 1(fixture manifest による E2E 検証手順)
└── tasks.md             # Phase 2(/speckit.tasks・本コマンド外)
```

### Source Code (repository root)

```text
probability/src/horseracing_probability/
└── calib_activation.py          # 新: load_calibration + Activation + ActivationMode + Profile(fail-closed)

training/src/horseracing_training/
├── calib_manifest.py            # 拡張: schema v1→v2(artifact_scope/activation_eligible additive)
└── cli.py                       # dispersion-pcal を manifest 直読に(US3)/ NOTE 更新

betting/src/horseracing_betting/
└── cli.py                       # _fit_product_p_calibrator に manifest 経路 + --calib-manifest/--calib-mode

serving/src/horseracing_serving/
├── pipeline.py                  # _fit_stage_discount に manifest 経路 + run_serving/backfill に calib 引数
└── __main__.py (cli)            # predict/predict-backfill に --calib-manifest/--calib-mode

api/src/horseracing_api/
├── dispersion.py                # load_p_calibrator を load_calibration 直読に(選択 run の model と照合)
└── routers/predictions.py       # 選択 run の model を dispersion に渡す(FR-020)

live/src/horseracing_live/
├── orchestrate.py               # refresh_range/_refresh_one に calib_manifest を通す(既存 p_calibrator 経路)
└── cli.py                       # refresh に --calib-manifest/--calib-mode

ops/src/horseracing_ops/
└── runner.py                    # serving/recommend subprocess argv に --calib-manifest を伝播

tests/  … 各パッケージ配下       # parity / leak-guard / fail-closed / 冪等 / entry-path 一致
```

**Structure Decision**: 新規は 1 モジュール(`probability/calib_activation.py`)のみ。他は既存の
fit 呼び出し点を manifest 経路に分岐させ、各 entry path(CLI/live/ops)に flag を通す薄い結線。
`build_manifest` の schema v2 拡張だけが training 側の変更。`deploy/`(README の read-only 運用ノート・
docs のみ、D9)。**db/front/admin/migration には触れない**(schema-zero)。

## Complexity Tracking

> Constitution Check 全 PASS(違反なし)のため空。
