# Data Model: Evaluation Contract v2 & Historical Freeze

**Feature**: 073 | **スキーマ変更**: なし(migration なし)。すべて disk artifact + manifest + 既存 JSONB。

## 1. ModelRecipe(拡張・training/recipe.py)

既存 frozen dataclass に 1 フィールド追加。

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `calibration_split_unit` | `str` | `"race_count_v1"` | calibration の train/calib 分割単位。`race_count_v1`=distinct race 数分割(既存 `split_train_by_time`)/ `race_day_v1`=開催日単位(既存 `split_train_by_day`) |

**recipe_hash 規則(back-compat canonicalization)**: 値が `race_count_v1`(legacy 既定)のとき `meta()` に含めない → 既存 recipe_hash と byte 一致。`race_day_v1` のとき含める → recipe_hash と model_version が必ず変わる。

**検証**: split 戦略変更で recipe_hash が変わる / 同一 model_version で split を変えた再学習は拒否 / `race_count_v1` の recipe_hash が既存値と一致。

## 2. 採用判定 artifact(eval/paired.py, PairedReport 拡張)

| フィールド | 型 | 説明 |
|---|---|---|
| `decision` | enum `ADOPT`/`REJECT`/`NO_DECISION` | 単一機械判定(旧 `adopted: bool` を置換、`ADOPT`⇔旧True) |
| `main_gate` | 構造体 | primary / stat_guard / recent_guard / top_noninferior / calibration(診断用に個別も保持) |
| `subgroup_guard` | 構造体 | critical subgroup(2026_only/nk/2026_nk)の三値 intersection-union(069 既存) |
| `decision_reason` | str | REJECT/NO_DECISION の理由(どの gate / subgroup / 期間不足か) |
| `evaluation_contract_version` | str | `v2`(過去 verdict は `v1`) |
| `gate_config_hash` | str | canonical gate-config の hash |
| `source_hash` / `result_hash` / `race_set_hash` | str | 入力データの決定論 hash |
| `candidate_recipe_hash` / `base_recipe_hash` | str | 両 arm の recipe_hash |
| `candidate_artifact_checksum` / `base_artifact_checksum` | str | 両 artifact の checksum |
| `started_all_set` | 集合記述 | started-all 評価に含めた馬集合と除外理由 |
| `determinism_proof` | 構造体 | 2 回実行の指標差(< 許容誤差) |
| `ece_by_subset` | dict | 全体 + 確率帯 + odds帯 + p帯 + q帯 + 共通tail mask(各帯: 境界/欠損bucket/最低件数/最低開催日数/NO_DECISION) |
| `bootstrap` | 構造体 | primary estimator(`race_day_cluster_bootstrap_ci_v1`)+ v2 感度(2/3/4日・週・開催、diagnostic) |

**状態遷移**: なし(append-only、1 評価=1 レコード)。

## 3. gate 判定の真理値

| 条件 | decision |
|---|---|
| main PASS ∧ 全 critical subgroup PASS | `ADOPT` |
| 主指標 FAIL ∨ 十分標本の critical subgroup が FAIL | `REJECT` |
| 評価期間 < eval_window ∨ 開催日数不足 ∨ critical subgroup 標本 < no_decision_min_days ∨ 必須データ欠損 | `NO_DECISION` |
| confirmatory mode で config 未知/欠落 ∨ 評価期間不一致 ∨ gate_config_hash 不一致 | 型付きエラー(fail-closed、判定を返さない) |

## 4. legacy 凍結レコード(create-only disk artifact `freeze_073.json` + committed spec copy)

> **実装時の設計変更(FR-011 遵守)**: 当初 `metrics_summary` JSONB への記録としていたが、active DB 行への書込を避けるため **create-only disk artifact** に変更。`artifacts/` は gitignore のため、committed の正本は `specs/073-eval-contract-correctness/legacy-freeze-lgbm-063.json`。append-only(同一内容は冪等・異内容は fail-closed)。

| フィールド | 説明 |
|---|---|
| `model_version` | **`lgbm-063`**(2026-07-15 実 DB 確定・features-017。quickstart §0) |
| `calibration_split_unit` | `race_count_v1`(凍結) |
| `artifact_digest` | model=`1a85b035…` / calibrator=`4babdda7…` / preprocessor=`cf1d518d…`(062/063 byte 一致) |
| `frozen_at` | 凍結時刻 |

**不変条件**: この digest の serving 予測が feature 前後で byte 一致(16 頭 mismatch 0)。

## 5. 070 supersession 記録(specs/070 + docs、append-only)

| フィールド | 説明 |
|---|---|
| `feature` | 070-past-market-bundles |
| `status_matrix` | F03=rejected / F04=rejected(NOT_RUN) / F05=rejected(NOT_RUN)、registry unwired |
| `superseded_by` | features-018 復帰(features-019 revert) |
| `commit_hash` / `verdict_artifact_hash` | 過去 commit / verdict の参照(過去文書は書き換えない) |

## 6. prospective holdout 事前登録レコード(DORMANT・docs/plan)

| フィールド | 説明 |
|---|---|
| `state` | `DORMANT`(または `AWAITING_CAPTURE`)。この feature では STARTED にしない |
| `hypothesis` / `feature_formula` / `thresholds` / `primary_metric` / `stopping_rule` | 事前登録フォーマット(空欄可・器のみ) |
| `time_to_signal_estimate` | 必要 settled bet 数・暦月数(オッズ供給後に算出) |
| `start_preconditions` | capture 稼働・immutable recipe・停止規則・最初の対象レース(全て揃って初めて時計開始) |

## Key entity 間の関係

- ModelRecipe.`calibration_split_unit` → recipe_hash → 採用判定 artifact の `*_recipe_hash`。
- legacy 凍結レコードの `model_version` → SC-005 の parity oracle。
- 採用判定 artifact の `evaluation_contract_version` が v1/v2 を分け、過去 verdict(v1)を不変に保つ。
