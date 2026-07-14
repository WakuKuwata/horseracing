# Phase 1 Quickstart: 070 過去市場 F03/F04/F05 検証手順

069 の F02 + subgroup ゲート基盤の上で、F03/F04/F05 を features-019 純加算し、段階評価で 1 bundle ずつ採否する検証手順。

**前提**: DB 稼働(port 15432、`DATABASE_URL='postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing'`)、069 実装 merge 済(features-018 / lgbm-064-f02acc candidate / lgbm-063 active)。**列名は spec.md 正本**。

---

## 1. 単体テスト(数式・leak・parity)

```
uv run --project features pytest
uv run --project training pytest
uv run --project serving pytest
```

**期待**:
- F03: percentile ∈[0,1]・competition rank tie(行順シャッフル不変)・**popularity-only complete-field**・rank_obs_count が F02 obs と独立。
- F04: finish_residual(finished, 分母 **N_started**)・win_residual mean10(started, 068 と行一致)・resid_sd5(ddof=1)・q は F02 共有。
- F05: **2 群**(support/residual)・λ=5 縮約(最新cell + target直前parent を別as-of→縮約)・**軸別 valid count**(実セルのみ)・pool-end 非依存。
- leak-guard: 対象レース market/results を変えても不変、過去 q/rank/results で変化、`asof_pm_*` 命名。
- **additive parity**: `test_*_is_purely_additive`(F03/F04/F05a/F05b 各群 = 右キー一意 + 列名 disjoint)。

---

## 2. features-019 の共有列 byte-parity(model-input 137列)

```
uv run --project features pytest -k "parity_019"
```

**期待**: 共有137列(materialized 112 ではない・codex B1/#4)が check_exact + check_dtype 一致。新群のみ増分。

---

## 3. serving compat(lgbm-063 / lgbm-064 byte-parity)

```
uv run --project serving pytest -k "compat"
```

**期待**: features-019 registry 下で lgbm-063(features-017)/ lgbm-064(features-018)が compat-load・予測 win prob byte 一致(16頭 mismatch 0)。compat map は 018/017 両直接 pin(**完全 hash**)。**同一版で列 subset を drop した artifact は NOT_SERVABLE**(F03 置換・未採用 F04/F05 drop の最終 candidate=期待挙動)。

---

## 4. materialize 再生成(features-019)

```
uv run --project features horseracing-features materialize \
  --database-url "$DATABASE_URL" --out artifacts/features.parquet
```

**期待**: materialized 列 = **112(features-018)+ 新19 ≈131**・**source_fingerprint は 069 と同一**(新ソース列なし)・in-memory bit 一致・018 parquet は再利用不可。

---

## 5. 段階評価(per-bundle paired-eval + subgroup)

contracts/cli.md の完全 recipe を operator が順に実行(gate-config `staged_evaluation` は記録用)。全段 `--from 2019-01-01 --to 2026-07-12`。

```
# 段1 F03置換(例) — 詳細と段2-5は contracts/cli.md
uv run --project training horseracing-training paired-eval --database-url "$DATABASE_URL" \
  --candidate "pl_topk:isotonic:0.3:drop=past_market,pm_expectation_residual,pm_conditioned_support,pm_conditioned_residual" \
  --active    "pl_topk:isotonic:0.3:drop=pm_rank_robust,pm_expectation_residual,pm_conditioned_support,pm_conditioned_residual" \
  --from 2019-01-01 --to 2026-07-12 --subgroups \
  --gate-config specs/070-past-market-bundles/gate-config.json --json out/f03.json
```

**期待(各段)**: winner NLL 差 + block bootstrap CI + subgroup(2026_only/nk/2026_nk 非悪化 three-way)。**採否 = `report.gate.adopted AND report.subgroups.subgroup_guard`**(operator が両 boolean を AND・069 同型)。診断=各 subgroup の absolute winner NLL 水準併記(paired 相殺で 2026/nk の死を可視化・069)。段は ADOPT/REJECT/NO_DECISION、採用群のみ次 baseline に前進。

---

## 6. candidate 登録(採用 bundle 合成, accuracy-first)

```
uv run --project training horseracing-training train-evaluate --database-url "$DATABASE_URL" \
  --objective pl_topk --calibration isotonic \
  --target-encode jockey_id,trainer_id,venue_code \
  --drop-groups "<未採用群>" --register-candidate \
  --artifacts-dir "$(pwd)/artifacts" \
  --model-version "lgbm-0XX-pmbundle"
```

**期待**: default(lgbm-063)不変・accuracy-first candidate 登録(**NOT_SERVABLE**=同一版列 subset drop)。**artifacts-dir 絶対パス必須**・**target-encode で評価 recipe 再現**・フラグは **`--model-version`**。

---

## 7. coverage 監査

```
uv run --project training horseracing-training coverage-audit \
  --database-url "$DATABASE_URL" --from 2019-01-01 --to 2026-07-12 --json out/cov.json
```

**期待**: 年 × ID source(canonical/nk:)× obs 帯(069 実装のまま・`--group` は無い)。2026/nk: の被覆を確認。

---

## 完了条件

- [ ] F03/F04/F05 単体テスト緑(spec 正本列名・数式・leak・additive parity・2 群)
- [ ] features-019 共有137列 byte-parity 一致
- [ ] lgbm-063/064 compat-load byte-parity(同一版列 subset drop の NOT_SERVABLE 確認)
- [ ] materialize 再生成(fingerprint 不変)
- [ ] 段階評価 各段 verdict 記録(`gate.adopted AND subgroup_guard`)
- [ ] 採用 bundle を accuracy-first candidate 登録(default 不変・target-encode 指定)
- [ ] coverage 監査記録
