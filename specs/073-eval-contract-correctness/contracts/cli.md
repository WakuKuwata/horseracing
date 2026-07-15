# CLI Contract: Evaluation Contract v2

**Feature**: 073 | 対象: `training` CLI の paired-eval 系 + `eval` の bootstrap 内部 API。**API/OpenAPI/front には触れない**(realized 改名=075)。

## training paired-eval(拡張)

```
uv run --project training python -m horseracing_training.cli paired-eval \
  --candidate <recipe.json|model_version> \
  --active    <recipe.json|model_version> \
  --from <YYYY-MM-DD> --to <YYYY-MM-DD> \
  --gate-config <path> \
  [--subgroups] [--seed 42] [--num-threads 1] [--confirmatory] [--json]
```

**変更点**:
- 出力の採否は単一 enum `decision ∈ {ADOPT, REJECT, NO_DECISION}`(旧 `adopted: bool` を置換)。`--json` は `decision`・`decision_reason`・`main_gate`・`subgroup_guard`・監査 hash 群・`ece_by_subset`・`bootstrap`(primary + v2 感度)を含む。
- `--confirmatory` 指定時、gate-config 未知/欠落・評価期間不一致・`gate_config_hash` 不一致は**型付きエラーで即終了**(判定を返さない)。
- `eval_window` / `no_decision_min_days` を実判定に結線。期間・開催日・subgroup 標本が不足すると `NO_DECISION`。
- read-only(DB 書込なし)。既存 `--use-materialized` 等の挙動は不変。

**後方互換**: `--json` に旧 `adopted` を `decision == "ADOPT"` として併記(既存消費側の破壊を避ける)。過去 verdict は `evaluation_contract_version=v1` として区別。

## gate-config(拡張フィールド)

既存 069/068 gate-config に追加(OOS 前固定・III):

| キー | 説明 |
|---|---|
| `evaluation_contract_version` | `"v2"` |
| `eval_window` | 評価窓(既存)。実判定に結線 |
| `no_decision_min_days` | critical subgroup の最低開催日数(既存 `10`)。underpowered→NO_DECISION |
| `ece_subsets` | 確率帯/odds帯/p帯/q帯/tail の境界・欠損bucket・最低件数・最低開催日数 |
| `tail_mask` | 事前登録共通 mask または `active_result_blind` 指定(arm 固有 tail は diagnostic) |
| `bootstrap.primary` | `race_day_cluster_bootstrap_ci_v1` |
| `bootstrap.sensitivity` | `[2d, 3d, 4d, week, meeting]`(diagnostic、gate の AND にしない) |

**改変拒否**: OOS 結果生成後の gate-config 変更を拒否するテスト(068 既存 `gate artifact 改変拒否` を踏襲)。

## eval bootstrap(内部 API 改名)

| 旧 | 新 |
|---|---|
| `moving_block_bootstrap_ci` | `race_day_cluster_bootstrap_ci_v1` |

**契約**: 改名後も同一入力で数値 byte 一致(golden test)。v2 感度関数は別名で追加(primary を置換しない)。呼び出し元(paired.py 等)を全置換。deprecation alias は残さない(内部 API)。

## recipe(training/recipe.py)

`ModelRecipe` に `calibration_split_unit`(既定 `race_count_v1`)。CLI で recipe.json を渡す際に指定可能。`race_day_v1` を指定した学習・昇格は**本 feature のスコープ外**(別 feature)。

## 破壊しない契約

- serving / api / front / OpenAPI / DB schema は不変。
- 既存 active モデルの serving 予測 byte 不変(SC-005)。
- 過去 verdict(068/069/070)は不変(FR-015)。
