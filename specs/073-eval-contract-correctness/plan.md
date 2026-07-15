# Implementation Plan: Evaluation Contract v2 & Historical Freeze

**Branch**: `073-eval-contract-correctness` | **Date**: 2026-07-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/073-eval-contract-correctness/spec.md`

## Summary

評価という物差しの壊れた部分を、**新データ・スキーマ変更・再学習なし**で是正する基盤 feature。(1) 採用判定を単一三値 enum(ADOPT/REJECT/NO_DECISION)にし `eval_window`/`no_decision_min_days` を実結線・started-all を harness 本体に統合・監査 artifact に契約 version と hash 群を残す(US1)。(2) calibration の split 単位を `ModelRecipe.calibration_split_unit ∈ {race_count_v1, race_day_v1}` として明示化し既存 active を `race_count_v1` で digest 凍結(US2、068 の「日単位 split 完了扱いだが本番は race-count」不一致の根治)。(3) `moving_block_bootstrap_ci` を実体一致名 `race_day_cluster_bootstrap_ci_v1` に改名(数値維持)+block 幅感度・過去 verdict を `evaluation_contract_version=v1` として凍結(US3)。(4) 070 status matrix 凍結・2008–2026 を development evidence 明記・prospective holdout を DORMANT で事前登録(US4)。

**技術方針**: 校正リーク是正(OOF-faithful two-gamma/stage discount)と realized 改名(API 破壊的変更)は codex レビューに従い**後続 feature 074/075 に分離**。本 feature は既存 active モデルの serving 予測を**バイト不変**に保つ(再学習・昇格・active 書換なし)。

**codex 設計レビュー**: 取得済み(`docs/plan/codex-073-review.md`、`codex exec --sandbox read-only` 2026-07-15)。主要指摘の採否は下記 Constitution Check 直後の「codex second opinion 記録」および research.md D1–D8 に記録。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: numpy / scikit-learn / pandas / lightgbm(既存)、SQLAlchemy 2.0(read-only)。新規依存なし。

**Storage**: PostgreSQL 16(**読み取りのみ・migration なし**)。監査 artifact / 校正 split 明示 / legacy 凍結 / 070 supersession / dormant 事前登録は disk artifact + manifest + 既存 `model_versions.metrics_summary`(JSONB)で完結。

**Testing**: pytest + testcontainers(eval / training 既存スイート)。実 DB paired E2E は 2 回実行の決定論確認を含む。

**Target Platform**: ローカル CLI / オフライン評価(serving/api には触れない。US3 の bootstrap 改名は eval のみ)。

**Project Type**: 既存マルチパッケージ(`eval/` `training/` 中心。`probability/` は 074 で扱うため本 feature では読み取り参照のみ)。

**Performance Goals**: N/A(評価契約の正しさが目的。数値改善を目標にしない)。決定論再実行の許容誤差は gate-config に事前登録(068 の `< 1e-9`・単一 thread を踏襲)。

**Constraints**: 既存 active モデルの serving 予測バイト不変(SC-005)。スキーマ変更ゼロ。過去 verdict(068/069/070)を上書きしない。

**Scale/Scope**: 評価対象は既存の walk-forward OOS 窓(2019–2026 は development evidence として凍結)。触れるコードは `eval/src/horseracing_eval/{paired,bootstrap,harness,subgroups}.py`、`training/src/horseracing_training/{recipe,calibration,predictor,artifacts,cli}.py`。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: raceId/ラベル契約は不変。この feature は評価契約のみ変更し ID/ラベル定義に触れない。**PASS**。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 評価派生値(NLL/ECE/CI/gate 判定/tail mask)をモデル特徴に還流しない(FR-019、leak-guard test)。started-all 統合は評価母集団の是正でありリーク面を増やさない。tail mask は**事前登録の共通 mask か active/base 由来の result-blind mask**で arm 固有 tail を diagnostic に降格(FR-006)=結果参照でarmごとに評価集合が動く問題を回避。**PASS**。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 本 feature 自体が評価契約の是正。三値 gate・帯別 ECE・決定論・監査 artifact は III を強化する。gate-config は OOS 結果を見る前に事前登録し、v2 bootstrap 感度で過去 verdict を遡及変更しない(FR-015)。**PASS**。
- [x] **IV. 確率整合性**: モデルの確率値を変更しない(FR-021)。started-all 評価は Σ 整合を壊さず母集団を started に揃えるだけ。**PASS**。
- [x] **V. 再現性・監査**: 監査 artifact に evaluation_contract_version・gate-config hash・source/result/race-set hash・recipe hash・checksum・started-all 集合・決定論証跡(FR-005)。legacy 凍結・070 supersession は append-only・過去 verdict 不変(FR-015/016)。オッズ上書き方針は不変。**PASS(強化)**。
- [x] **VI. feature 分割規律**: スキーマ変更ゼロ・migration なし。UI/API に触れない(realized 改名=API 破壊的変更は 075 に分離)。校正リーク是正=074 に分離。1 feature 1 関心。**PASS**。
- [x] **品質ゲート**: codex second opinion 取得済み(`docs/plan/codex-073-review.md`)。両案差分と採否を下記に記録。**PASS**。

### codex second opinion 記録(差分と採否)

| codex 指摘 | 採否 | 反映先 |
|---|---|---|
| 日単位 split を active に即適用するな。split を recipe 意味論化+legacy 凍結、071 は再学習なし | **採用** | US2 / FR-009〜012 / research D1 |
| recipe/artifact が immutable 契約未達(split戦略・feature hash・checksum 等未含有)、create-only 化が必要 | **一部採用・一部分離** | 監査 artifact の hash 群は US1(FR-005)。base model artifact の create-only 化は **074 に分離**(本 feature は再学習しないため既存 artifact を凍結参照するのみ) |
| immutable 化だけでは校正リークは直らない(latest-run 世代非限定・full-history 由来の非OOS) | **採用・分離** | **074 の中核**。本 feature の ECE は raw+model-internal calibrated までに限定(FR-007) |
| gate は boolean AND でなく三値単一 enum、window/min-days 実結線、confirmatory で fail-closed | **採用** | US1 / FR-001/002 / research D2 |
| started-all を harness 本体に統合、監査 artifact 必須化 | **採用** | US1 / FR-003/005 / research D3 |
| bootstrap 改名(数値維持)+v2 感度、過去 verdict を contract_version=v1 で凍結・上書き禁止 | **採用** | US3 / FR-013〜015 / research D4 |
| ECE 4 段階分離・stage discount は top2/top3・arm 固有 tail は diagnostic・共通/result-blind mask | **採用(段は 074 依存部を除く)** | FR-006/007 / research D5 |
| realized 改名は公開 API 破壊的変更(schema/front/admin/OpenAPI 原子 migration) | **採用・分離** | **075 に分離**(本 feature では触れない) |
| 070 は status matrix 凍結+append-only supersession、holdout は DORMANT | **採用** | US4 / FR-016/018 / research D6 |
| 将来 ROI 台帳は憲法 V 改定が前提 | **採用・記録** | spec 依存節・research D7 |
| active が 062 か 063 か DB で確定(推測固定禁止) | **採用** | 着手時 DB 確定 / research D8 / quickstart 前提 |

**保留・不採用**: なし(全指摘を採用または後続 feature に分離)。追加リスク: recipe に `calibration_split_unit` を足すと `recipe_hash` が変わりうる → 既存 active の recipe_hash 互換を back-compat canonicalization で守る(research D1、058 の hash pinning 前例)。

## Project Structure

### Documentation (this feature)

```text
specs/073-eval-contract-correctness/
├── plan.md              # This file
├── research.md          # Phase 0: D1–D8 決定
├── data-model.md        # Phase 1: 採用判定 artifact / recipe 拡張 / gate enum / supersession / dormant 事前登録
├── quickstart.md        # Phase 1: 実 DB 検証手順(active 確定→paired-eval 三値→決定論→parity)
├── contracts/
│   └── cli.md           # paired-eval の三値 enum 出力・gate-config 拡張・bootstrap 改名の CLI 契約
└── tasks.md             # /speckit-tasks で生成(この plan では作らない)
```

### Source Code (repository root)

```text
eval/src/horseracing_eval/
├── paired.py            # GateResult を三値 enum 化(_build_gate 統合)・window/min-days 結線・監査 artifact
├── bootstrap.py         # moving_block_bootstrap_ci → race_day_cluster_bootstrap_ci_v1 改名+v2 感度
├── harness.py           # started-all を本体 _score_race に統合(finished-only 解消)
└── subgroups.py         # 既存 three_way / subgroup_guard を主判定 enum に接続

training/src/horseracing_training/
├── recipe.py            # ModelRecipe に calibration_split_unit 追加 + recipe_hash back-compat canonicalization
├── calibration.py       # 既存 split_train_by_time / split_train_by_day を recipe 経由で選択(既定=race_count_v1)
├── predictor.py         # split 選択を recipe 由来に(既定は現行 split_train_by_time と byte 一致)
├── artifacts.py         # legacy 凍結レコード(digest pin)・監査 artifact 出力(create-only 化は 074)
└── cli.py               # paired-eval / calib 系サブコマンドの三値出力・gate-config

specs/070-past-market-bundles/   # 070 status matrix 凍結(supersession 記録・過去文書は不変)
docs/plan/                       # development evidence 明記 / dormant 事前登録フォーマット

eval/tests/ · training/tests/    # 三値真理値表・境界(9日/10日・空window・subgroup不足)・
                                 # split→recipe_hash変化・同version split変更再学習拒否・
                                 # bootstrap v1 golden(数値不変)・started-all E2E 2回一致・
                                 # 既存 active serving byte 不変(16頭 mismatch 0)・leak-guard
```

**Structure Decision**: 既存の `eval/`(評価契約)と `training/`(recipe/split/artifact)に閉じる。`probability/`(校正リーク)と `api/`(realized 改名)は後続 feature 074/075 に分離するため本 feature では触れない。DB・migration・serving・front には触れない。

## Complexity Tracking

> Constitution Check に違反なし。分割で複雑度を下げている(074/075 分離)。

| 論点 | 判断 |
|---|---|
| recipe に split 単位を足すと既存 recipe_hash が変わる懸念 | back-compat canonicalization(legacy 既定 `race_count_v1` は hash に含めず、`race_day_v1` のみ hash 変化)。058 の hash pinning 前例。research D1 で確定。serving 予測バイトは artifact 由来で recipe_hash 非依存のため SC-005 は独立に成立 |
| gate 統合で過去の boolean adopted 判定と非互換になる懸念 | 三値 enum を新設し、`ADOPT` は旧 `adopted=True` と後方一致するようマッピング。過去 verdict は contract_version=v1 として保持(FR-015) |
