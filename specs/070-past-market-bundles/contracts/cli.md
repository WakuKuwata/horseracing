# Phase 1 Contract: 070 CLI(段階評価 + candidate 登録)

069 の `paired-eval --subgroups` / `train-evaluate --register-candidate` / `_expand_group_drops` を再利用。**新 CLI サブコマンドは追加しない**(既存フラグの組合せ)。API/OpenAPI 変更なし。**gate-config `staged_evaluation` は CLI 非消費**(記録用)→ operator が各段の完全 recipe を手で実行(codex 論点3)。**最終 verdict = `gate.adopted AND subgroup_guard`**(driver が両 boolean を読んで AND・codex B2)。

---

## 1. features materialize(既存・再生成)

```
uv run --project features horseracing-features materialize \
  --database-url "$DATABASE_URL" --out artifacts/features.parquet
```

- features-019 で列集合が増える → **一度 re-materialize**(fingerprint algo=fp-v2 不変・列追加のみ・018 parquet は version mismatch で再利用不可)。
- **source_fingerprint は 069 と同一**(新ソース列なし)。

---

## 2. 段階評価(per-bundle paired-eval, 069 再利用)

recipe 文字列は `objective:calibration[:calib_frac][:drop=g1,g2]`(069 `_recipe_from_spec`・`_expand_group_drops` で群→列)。**ベースライン arm は `--active`**(`--baseline` は train-evaluate 専用)。全段 **`--from 2019-01-01 --to 2026-07-12`**(gate-config `eval_window` は CLI で渡す・codex B2)。

```
# 段1: F03 置換(両arm F04/F05 群 drop)
uv run --project training horseracing-training paired-eval --database-url "$DATABASE_URL" \
  --candidate "pl_topk:isotonic:0.3:drop=past_market,pm_expectation_residual,pm_conditioned_support,pm_conditioned_residual" \
  --active    "pl_topk:isotonic:0.3:drop=pm_rank_robust,pm_expectation_residual,pm_conditioned_support,pm_conditioned_residual" \
  --from 2019-01-01 --to 2026-07-12 --subgroups \
  --gate-config specs/070-past-market-bundles/gate-config.json --json out/f03.json

# 段2: F04 追加(両arm F05 群 drop) — 現base は F03 verdict 反映後(<F03D>=ADOPT なら past_market・REJECT なら pm_rank_robust)
uv run --project training horseracing-training paired-eval --database-url "$DATABASE_URL" \
  --candidate "pl_topk:isotonic:0.3:drop=<F03D>,pm_conditioned_support,pm_conditioned_residual" \
  --active    "pl_topk:isotonic:0.3:drop=<F03D>,pm_expectation_residual,pm_conditioned_support,pm_conditioned_residual" \
  --from 2019-01-01 --to 2026-07-12 --subgroups --gate-config ... --json out/f04.json

# 段3: F05 support(両arm residual drop) — base は F03+F04 verdict 反映後
#   <F04D> = F04 ADOPT なら "(空=何もdrop足さない)"・REJECT なら "pm_expectation_residual"
uv run --project training horseracing-training paired-eval --database-url "$DATABASE_URL" \
  --candidate "pl_topk:isotonic:0.3:drop=<F03D>,<F04D>,pm_conditioned_residual" \
  --active    "pl_topk:isotonic:0.3:drop=<F03D>,<F04D>,pm_conditioned_support,pm_conditioned_residual" \
  --from 2019-01-01 --to 2026-07-12 --subgroups --gate-config ... --json out/f05_support.json

# 段4: F05 residual — F04 が ADOPT の時のみ(=<F04D> は空、base に pm_expectation_residual 含む)
uv run --project training horseracing-training paired-eval --database-url "$DATABASE_URL" \
  --candidate "pl_topk:isotonic:0.3:drop=<F03D>,<F05supportD>" \
  --active    "pl_topk:isotonic:0.3:drop=<F03D>,<F05supportD>,pm_conditioned_residual" \
  --from 2019-01-01 --to 2026-07-12 --subgroups --gate-config ... --json out/f05_residual.json
#   <F05supportD> = 段3 REJECT なら "pm_conditioned_support"・ADOPT なら "(空)"

# 段5: stack-safety-check — 全採用群合成(現base=F03勝者+採用F04+採用F05) vs lgbm-064-f02acc 系
#   独立 confirmatory ではない=同一OOS(TE-free↔TE・非独立確認を artifact に明記)
```

- **verdict 分岐プレースホルダ**(勝者のみ base 累積・両 arm 対称=FR-006a): `<F03D>`=段1 ADOPT で `past_market`(058敗者)・REJECT で `pm_rank_robust`(F03敗者)を drop。`<F04D>`=段2 ADOPT で空(pm_expectation_residual を base に keep)・REJECT で `pm_expectation_residual` を drop。`<F05supportD>`=段3 ADOPT で空・REJECT で `pm_conditioned_support` を drop。**gate-config `staged_evaluation` matrix(`_base`/`num`/`both_drop`)が正本**、T006a が matrix ↔ 段1 literal recipe + F03/F04 verdict 分岐の downstream base 累積を機械照合(analyze F1/F2)。
- **TE 規約(analyze F1)**: paired-eval の recipe grammar(`objective:calibration[:calib_frac][:drop=]`)は **target-encoding トークンを持たない → 各段の採否は TE-free re-fit 上の特徴の限界寄与**(069 の F02 評価と同じ規約)。TE は最終登録(T033 の `train-evaluate --target-encode`)でのみ付与し、その accuracy は標準 train-evaluate ゲートで再確認する(paired-eval verdict=特徴の帰属・TE は直交)。この TE-free↔登録 config の差は既知の残リスクとして記録。
- **stage-gating の "ADOPT" 定義(analyze A1)**: 段の前提となる "F04 ADOPT" 等は **合成 verdict = `gate.adopted AND subgroup_guard`** を指す(FR-006a の最終 verdict と同一定義)。
- **eval_window の同期(analyze A1)**: gate-config `eval_window`(2019-01-01→2026-07-12)は CLI 非消費=**operator の `--from/--to` は gate-config `eval_window` と byte 一致必須**(事前登録窓と実行窓の drift 防止)。
- `--subgroups`: 069 の 2026_only / 2026_field_has_nk / canonical / nk / 2026_nk 差 + block bootstrap CI。**採否 = `report.gate.adopted AND report.subgroups.subgroup_guard`**(operator が `--json` 出力の `gate.adopted`[bool] と `subgroups.subgroup_guard`[bool・`subgroups.critical` と同階層]を AND=069 と同じ read-time 判定)。
- coverage 帯 subgroup は obs_count 未配線で live 生成されない(critical に含まず diagnostic)。

---

## 3. candidate 登録(accuracy-first, 069 再利用)

採用 bundle 合成を accuracy-first candidate として登録(default 不変)。**production accuracy-first recipe(lgbm-064-f02acc)と同じ target-encoding 列集合を指定=`jockey_id,trainer_id`**(036 由来の lineage・CLI 既定の `...,venue_code` は使わない=lgbm-064 と TE 列が食い違うと T032 stack 比較を confound する・analyze I1。実列集合は T002 で lgbm-064 metadata から裏取り)。paired-eval は §2 のとおり TE-free=登録は TE 付きで別(§2 の TE-free↔TE delta 注記参照・codex B4/analyze C1)。

```
uv run --project training horseracing-training train-evaluate --database-url "$DATABASE_URL" \
  --objective pl_topk --calibration isotonic \
  --target-encode jockey_id,trainer_id \
  --drop-groups "<未採用群>" \
  --register-candidate \
  --artifacts-dir "$(pwd)/artifacts" \
  --model-version "lgbm-0XX-pmbundle"   # ← フラグは --model-version(--model-label は存在しない)
```

- `--register-candidate`: `register_as_candidate=True`(default active を変えない)。
- `--drop-groups`: 群名を `_expand_group_drops` で列名展開。
- **登録 candidate は `NOT_SERVABLE_PENDING_PROFILE`**(同一版で 1 列でも drop=loader が fail-closed 拒否)。F03 置換だけでなく未採用 F04/F05 を drop した最終 candidate も該当。paired-eval は再fit で評価済のため serving 不要。
- **artifacts-dir は絶対パス**(相対だと ops 予測が weights_uri 解決に失敗=既知バグ)。
- 用途ラベル(display_name/purpose)は別途 `training set-model-label`(057)。

---

## 4. coverage 監査(069 再利用)

```
uv run --project training horseracing-training coverage-audit \
  --database-url "$DATABASE_URL" --from 2019-01-01 --to 2026-07-12 --json out/cov.json
```

- **`--group` フラグは無い**(codex B4)。`coverage-audit` は 069 実装のまま年 × ID source(canonical/nk:)× obs 帯を出力(憲法 V)。F03/F04/F05 の has_obs 別監査が要れば 069 の audit 拡張=別タスク。

---

## 契約不変性

- **スキーマ/API/OpenAPI/migration 不変**。
- 新 CLI サブコマンドなし。新群は registry(FEATURE_VERSION 019・compat 018/017 両直接 pin=**完全 hash**)・materialize 結線・F05 の 2 群のみ。
- eval verdict の tri-state AND は driver 適用(069 同型)。coverage 帯 live 化・stage resolver 自動化は任意/deferred。
