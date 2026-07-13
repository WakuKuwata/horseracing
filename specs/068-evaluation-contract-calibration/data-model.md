# Data Model: 評価契約の是正 + 校正分割の見直し

**Feature**: 068 | **Date**: 2026-07-12

**スキーマ変更なし・migration なし**。以下は in-memory の評価 dataclass と、既存 `model_versions.metrics_summary` JSONB への追記フィールドのみ。

## 1. 指標（eval/metrics.py 追加関数の出力）

| 指標 | 定義 | 母集団 | 種別 |
|---|---|---|---|
| `winner_nll` | レースごと `-log(p_winner)` の平均 | 勝者ちょうど1頭のレース | PRIMARY |
| `winner_nll_excluded` | winner NLL から除外したレース数（同着・勝者不在・未確定） | — | 監査 |
| `started_all_logloss` | per-horse LogLoss、DNF・失格=win0 | started 全馬 | SECONDARY |
| `started_all_brier` | 同上 Brier | started 全馬 | SECONDARY |
| `finished_logloss` | 現行指標（過去互換） | finished 馬のみ | 互換 |
| `ece_equal_width` | 固定10等幅 ECE（現行） | started 全馬 | 校正 |
| `ece_equal_mass` | 等質量ビン ECE | started 全馬 | 校正 |
| `ece_by_prob_band` | 確率帯別 ECE（帯境界は事前固定） | started 全馬 | 校正 |
| `ece_by_field_size` | 頭数別 ECE | started 全馬 | 校正 |

- winner NLL の `p_winner` は clip 後（`[clip,1-clip]`）で `log` を安定化。
- 確率帯境界・頭数バケットは実行前固定（OOSで動かさない、III）。

## 2. ModelRecipe（paired-eval / calib-split-eval の入力単位）

保存 artifact ではなく、**各 outer fold で再 fit するための処方**（codex C1）。

| フィールド | 内容 |
|---|---|
| `objective` | pl_topk 等（SOFTMAX_OBJECTIVES） |
| `calibration` | isotonic / temperature / race-normalized power / identity |
| `calib_frac` | 校正 holdout 比率（A=0.3 / B=0.1 / C,D=OOF なので該当なし） |
| `booster_alloc` | 「日単位末尾比率」or「全履歴refit」 |
| `feature_version` / `feature_cols` | 特徴 schema（全 arm 同一） |
| `target_encode_cols` / `te_smoothing` | TE 設定 |
| `seed` / `params` | 決定論 seed・LightGBM params |
| `market_offset` | **false 固定**（true は fail-closed 拒否、codex C3） |

既存 register 済みモデル（lgbm-062 等）を比較対象にする場合は、その metadata から ModelRecipe を復元して各 fold 再 fit する（保存 booster は使わない）。

**import 境界（analyze C1）**: `ModelRecipe` は `training/recipe.py` に置く。eval は `foldfit.PredictorFactory` Protocol（`(train_rows, fold) -> fitted predictor`）だけを受け取り、`ModelRecipe` 型を import しない。CLI が recipe → factory を構築して注入する。

## 2b. PredictorFactory（eval/foldfit.py の Protocol）

eval が training を import せずに各 fold 再 fit を駆動するための注入インターフェース。

| メンバ | 内容 |
|---|---|
| `fit(train_rows, fold) -> Predictor` | outer-train 全量で fit した predictor を返す（保存 booster を使わない） |
| `recipe_meta` | 監査用の plain dict（objective/calibration/booster_alloc/market_offset 等） |
| `recipe_hash` | recipe の決定的 hash |

CLI（training）が `ModelRecipe` から factory を生成し、`eval.paired` へ candidate/active の2 factory を渡す。

## 3. PairedEvalReport（eval/paired.py）

候補↔active の同一 race 集合 paired 比較結果。**特徴に流入しない**（II）。

| フィールド | 内容 |
|---|---|
| `candidate_recipe_meta` / `active_recipe_meta` | 比較する2 factory の recipe plain dict（training 型を持ち込まない、analyze C1） |
| `candidate_recipe_hash` / `active_recipe_hash` | 各 recipe の決定的 hash |
| `race_id_set_hash` | model-blind に先固定した race 集合 hash（不一致・片側欠落で fail-closed、codex C8） |
| `fold_boundaries` | walk-forward outer fold 境界（両者共通） |
| `eval_code_version` / `code_sha` | 評価コード version・git SHA（両者共通） |
| `source_fingerprint` / `manifest_hash` / `snapshot` | DB source / materialized manifest / repeatable-read snapshot・result/entry hash（codex C9） |
| `hash_contract` | 6種（research D7 C5 と一致、analyze H1）: `feature_schema_hash`・`raw_matrix_content_hash`（全arm同一）/ `model_race_set_hash`・`calib_race_set_hash`（arm別の race 分割 hash）/ `transformed_matrix_hash`・`model_artifact_hash`（arm別） |
| `winner_nll` | `{candidate, active, diff}` |
| `started_all` | `{logloss, brier}` × `{candidate, active, diff}` |
| `finished_compat` | 現行指標（candidate/active） |
| `ece` | equal_width / equal_mass / by_band / by_field_size（candidate/active） |
| `top2` / `top3` | LogLoss（candidate/active/diff、non-inferiority 判定用） |
| `periods` | `{all, recent_3y, recent_5y}` ごとの上記サブセット |
| `bootstrap_ci` | paired winner NLL 差の 95% CI（下限/上限/B/seed/block=開催日） |
| `gate` | GateResult（下記） |

**「全arm同一」のスコープ（analyze I3）**: `feature_schema_hash`/`raw_matrix_content_hash` が全arm同一という不変は **A/B/C/D の校正分割 arm（同一 feature_version）にのみ適用**する。一般の candidate-vs-active paired-eval（例 SC-001 の lgbm-062[features-017] vs lgbm-061[features-016]）は **feature schema が異なりうる**ため、schema/content hash の一致は要求せず、**`race_id_set_hash`・fold 境界・snapshot の一致のみ必須**（FR-003）。cross-feature-version paired-eval はこの緩和下でサポートする。

### GateResult（採用ゲート、FR-008）

| 条件 | 合格基準 |
|---|---|
| `primary` | candidate winner NLL < active winner NLL |
| `stat_guard` | paired 差 95% CI 上限 < 0 |
| `recent_guard` | 直近3年 **かつ** 5年の両窓で winner NLL 非悪化（どちらか悪化で不合格＝保守的 AND、analyze C2。該当raceなし窓は除外・報告） |
| `top_noninferior` | top2/top3 LogLoss 差 ≤ 事前固定 non-inferiority 幅 |
| `calibration` | **mean-ECE** が active 比 non-inferiority幅以内（worst-fold は監査報告のみ・ゲート非使用、analyze A1）。絶対 ECE 0.05 は非常停止上限 |

`adopted = all(conditions)`。各条件の合否と理由を保持。閾値・幅・seed・fold は実行前固定 artifact。

## 4. CalibrationSplitExperiment（training/calib_split_eval.py）

| フィールド | 内容 |
|---|---|
| `experiment_id` | A / B / C / D |
| `booster_alloc` | 「日単位末尾70%」「日単位末尾90%」「全履歴refit」（分割は開催日単位、codex C4） |
| `calib_source` | train holdout（最新30%/10%）/ **expanding strict-past OOF**（C/D、codex C6） |
| `calib_method` | isotonic（raw score）/ temperature（raw score）/ race-normalized power（正規化p、Σ=1保存） |
| `fixed` | `{feature_version, objective, seed}`（4条件で同一。bit-parity は非要求、codex C5） |
| `score_transfer_check` | C/D: OOF↔refit の raw score 分布移植可能性（**inner-valid**、悪化で B フォールバック） |
| `screen_inner` | 各outer foldのinner-validでのscreening（outer-valid非参照、codex C2） |
| `confirmation` | 勝ち候補のみ: screening非使用の独立windowで PairedEvalReport（vs active） |

## 5. TrainingProvenance（fit_info_ 追記 → metrics_summary JSONB）

既存 `fit_info_`（[predictor.py:236](../../training/src/horseracing_training/predictor.py)）に追加。既存行は遡及書換なし（次回学習から populate）。

| フィールド | 定義 | 現状 |
|---|---|---|
| `n_model_rows` | booster 学習行数 | **既存**（維持） |
| `n_calib_rows` | 校正フィット行数 | **既存**（維持） |
| `train_through` | train frame 全体の最大日 | **既存**（維持、意味は変えない） |
| `model_fit_through` | **booster 実学習の最終日**（model-fit 分割の最大 race_date） | **新規** |
| `calib_from` | 校正データ最小 race_date | **新規** |
| `calib_through` | 校正データ最大 race_date | **新規** |

- 校正分割ありの A/B では `model_fit_through < train_through`（最新30/10%を booster が学習しないことが可視化される）。
- 全履歴refit の C/D では `model_fit_through == train_through`（校正は OOF なので calib_from/through は OOF 対象期間を表す）。
- calib 退化（identity fallback）時は calib_from/through を null にする。

## 6. リーク境界（II）

- 指標・PairedEvalReport・CalibrationSplitExperiment・bootstrap CI・provenance は**すべて特徴に戻さない**。leak-guard test で「これらの値を変えてもモデル特徴が不変」を固定。
- win label（結果）は採点専用。started/finished の母集団分類は結果ラベルの参照だが特徴経路に無関係（既存 dataset と同じ）。
- 対象レース自身の市場・結果は評価入力にも特徴にも使わない。
