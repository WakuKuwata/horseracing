# CLI Contract: 評価契約 + 校正分割

**Feature**: 068 | **Date**: 2026-07-12

新規 CLI サブコマンド2つ（`training` パッケージ）。API・OpenAPI・DB は不変。

## `training paired-eval`

候補↔active を**ModelRecipe から各 outer fold で再 fit** して同一 race 集合で walk-forward 評価し、PairedEvalReport を出す（US1）。

```
training paired-eval \
  --candidate <recipe.json|model_version> \
  --active    <recipe.json|model_version|"db-active"> \
  [--from YYYY-MM-DD] [--to YYYY-MM-DD] \
  [--bootstrap-b 2000] [--seed 20260712] [--num-threads N] \
  [--noninferior-top2 0.0005] [--noninferior-top3 0.0005] \
  [--noninferior-ece 0.001] \
  [--gate-config <path>] \
  [--use-materialized] [--json <out.json>]
```

**契約**:
- candidate/active は **ModelRecipe**（data-model §2）。model_version 指定時は metadata から recipe を復元する。**保存 booster を過去 race に適用しない**（全履歴 fit の serving model は walk-forward 適用が in-sample、codex C1）。各 outer fold で両 recipe を再 fit し outer-valid を一度だけ予測する。
- recipe は **`market_offset=false` を fail-closed で要求**（true は対象 race odds を読む、codex C3）。
- 両者を**現DB・同一 materialized manifest・同一 race 集合・同一 fold 境界・同一評価コード version** で評価する。baseline を `metrics_summary` から読まない。
- `--seed` / non-inferiority 幅（top2/top3/**mean-ECE**）/ gate 閾値は実行前に確定し、レポートに記録する（III）。ECE 幅の初期アンカーは `0.001`（mean-ECE、gate-config で確定、worst-fold は監査のみ）。省略時は `--gate-config` の事前登録値、それも無ければ固定既定。gate artifact は OOS 結果生成後に変更不可（codex テスト）。
- race_id 集合を **model-blind に先に固定** し hash を両者で照合。不一致・片側予測欠落は typed error（fail-closed、post-prediction intersection を採らない、codex C8）。
- snapshot 監査: repeatable-read snapshot・result/entry hash・manifest hash・recipe hash・code SHA を記録（V、codex C9）。
- `--num-threads`: 省略時はデフォルト（multi-thread 可）。SC-002 の決定論検証は `--num-threads 1` を明示（bit 再現）。
- 出力: 標準出力に要約、`--json` で PairedEvalReport 全体（data-model.md §2）。
- eval は predictor-agnostic。CLI が `LightGBMPredictor` を注入する（020 循環回避）。
- **read-only**: DB へ書き込まない（採用の永続化は別途 register 系 CLI の領分、本コマンドは判定と artifact 生成のみ）。

**exit code**: 0=完走（adopted 可否は JSON 内）、非0=fail-closed（race集合不一致・モデル未収録・fold構成不能）。

## `training calib-split-eval`

A/B/C/D の校正分割実験を比較する（US2）。

```
training calib-split-eval \
  --experiments A,B,C,D \
  [--from YYYY-MM-DD] [--to YYYY-MM-DD] \
  [--seed 20260712] \
  [--derisk-recent-folds 3] \
  [--full-walk-forward-winners] \
  [--json <out.json>]
```

**契約**:
- 全 experiment で **feature_version・objective・seed を固定**（4条件で同一、FR-010）。差は calib_frac / booster 学習配分 / 校正方式のみ。bit-parity は非要求（TE encoder 母集団が変わる、codex C5）。
- A=`日単位末尾30% holdout + isotonic`、B=`日単位末尾10% + isotonic`、C=`全履歴refit + strict-past OOF temperature`、D=`全履歴refit + strict-past OOF race-normalized power`。分割は開催日単位（codex C4）、C/D の OOF は expanding strict-past（codex C6）。
- 校正作用空間: isotonic/temperature は raw score、power は race-normalized p（Σ=1保存、IV・codex C7）。
- C/D は raw score 分布移植チェックを **inner-valid** で行い、悪化構成は B にフォールバックし理由を記録（FR-011）。
- `--derisk-recent-folds N`: 各 outer fold の **inner-valid** で screening（outer-valid 非参照）。screening に使った fold を最終判定 CI に含めない（独立 confirmation window、FR-014・codex C2）。
- `--full-walk-forward-winners`: go 判定の構成のみ、screening 非使用の window で active と paired 評価（`paired-eval` 経路を内部再利用）。
- 校正方式の選択は各外側 fold の inner-valid だけで行う（外側 valid 非参照、FR-012、035/036 前例）。
- 校正後は 009 engine で レース内 Σ=1・順位保存（FR-013、IV）。
- 出力: experiment ごとの CalibrationSplitExperiment（data-model.md §4）。
- **feature-build 経路**: screening（T026）と confirmation window（T016 再利用）は**同一の feature-build 経路**を使う（既定は非 materialized。materialized を使う場合も 025 の bit-parity 保証により同値、analyze A1）。
- **read-only**: 実験は評価のみ。active 昇格はしない（別 CLI）。

**exit code**: 0=完走、非0=fail-closed。

## 既存 CLI への影響

- `train-evaluate`: **変更なし**（本feature は評価契約を並行追加。既存の adopt 経路は維持）。ただし provenance 追記（US3）で `fit_info_` に `model_fit_through`/`calib_from`/`calib_through` が乗るため、次回学習から metadata がリッチになる（後方互換・欠損は null）。
- `feature-eval` / `model-eval`: 変更なし。将来の Phase 3/4 は本feature の `paired-eval` ゲートを使うが、それは別 spec。

## 非対象（明示）

- API・OpenAPI・front・admin: 変更なし。
- DB migration・スキーマ: なし。
- adoption / register: 本feature は判定と artifact 生成まで。永続昇格は既存 register 系の領分。
