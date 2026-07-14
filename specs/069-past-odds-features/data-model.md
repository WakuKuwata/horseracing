# Data Model: 過去オッズ量特徴(F02)+ subgroup ゲート拡張

**Feature**: 069 | **Date**: 2026-07-13

**スキーマ変更なし・migration なし**。以下は特徴列・in-memory の eval dataclass のみ。

## 1. PastMarketSupport(F02, group `pm_core_strength`)

過去 started 行のオッズ由来 `s=log(q×N)` を馬単位 as-of 集約。grain=horse-history。source=race_horses.odds(過去 started 行)。history_boundary=strictly-before + 同日除外。特徴のみ・結果非参照。

| 列 | 定義 | 欠損 |
|---|---|---|
| `asof_pm_support_last` | 直近1有効観測の s | NaN(0観測) |
| `asof_pm_support_mean3` | 直近3観測の縮約平均(λ=2, prior=0) | NaN(0観測) |
| `asof_pm_support_mean5` | 直近5観測の縮約平均(λ=2, prior=0) | NaN(0観測) |
| `asof_pm_support_best5` | 直近5観測の max | NaN(0観測) |
| `asof_pm_support_career` | 全観測の縮約平均(λ=5, prior=0) | NaN(0観測) |
| `asof_pm_support_trend` | 直近3観測の時間順単回帰傾き | NaN(2観測未満) |
| `asof_pm_support_sd5` | 直近5観測の標本標準偏差(ddof=1) | NaN(2観測未満) |
| `asof_pm_obs_count` | 有効市場観測数(生 count) | 0(観測なし=事実) |
| `asof_pm_has_obs` | 観測≥1で1、0で0 | 0(事実) |

- s を持つ過去レース = started 全馬に有効オッズ(0<O<∞)がある complete-field レースのみ(D3)。
- 全パラメータ(recent-K=有効観測・λ・prior=0)は OOS 前固定(III、gate-config 記録)。
- 列名は odds/popularity トークンを含まない(`asof_pm_*`、leak-guard、058/041 idiom)。

## 2. 既存 past_market(058, group `past_market`)— 不変

`asof_mkt_rank_avg` / `asof_mkt_rank_norm_avg` / `asof_mkt_rank_best` / `asof_beat_mkt_avg`(popularity rank 由来)。**069 で削除・改変しない**(帰属分離、codex)。F02 と共存し、両方を accuracy-first candidate に入れる。

**market-history 集合の定義**(FR-012/T019 が参照、analyze M2): `market-history = {group past_market, group pm_core_strength}`。recipe で drop する時は **`FEATURE_GROUPS` を反転して各 group の展開列名を `drop_features` に渡す**(drop_features は列名タプルで group 名では効かない=fail-open、analyze F1)。default 意思決定支援モデルは market-history 両群の展開列を全 drop(p⊥q)、active arm は `pm_core_strength` の展開列のみ drop。

## 3. FEATURE_VERSION / compat

- FEATURE_VERSION: `features-017` → `features-018`(F02 純加算)。
- `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"] = {"features-017": "300b28a9…"}`(lgbm-063 hash pin。**この literal は T015 が lgbm-063 metadata.feature_hash を実測して一致検証してから T017 が registry に入れる**=無検証で使わない、analyze L1/V1)。
- 共有128列 byte-parity: additive-merge 構造担保 + 一度きり実測(check_exact/check_dtype)。
- source_fingerprint: **loader に `RaceHorse.odds` を追加(新ソース列、codex C4)** → fingerprint を odds 込みに拡張(056 前例、migration 不要=odds 列は既存)。materialize-safe を再担保(stale fail-closed)。058 は popularity のみ読んでいた。

## 4. SubgroupGateResult(US1, eval/paired.py 拡張)

068 の PairedReport に加算。**特徴に流入しない**(II)。**grain を分離**(codex C1):

- **race-level(winner NLL 損失差)**: 結果非依存の race 属性 = `2026_only`(race_date.year)・`2026_field_has_nk`(フィールドに nk: 馬が居る 2026 レース)。winner-conditioned なレース選択はしない。
- **horse-level(started-all per-horse loss)**: per-horse 属性 = `canonical` / `nk` / `2026_nk`(2026×nk:)/ coverage 帯 `cov_0` `cov_1_2` `cov_3plus`(厳密前 `asof_pm_obs_count`)。ID source の死活はここで見る。

**`2026_field_has_nk`(race-level)は report-only**(critical でない、analyze F1)。nk: の死活のガードは horse-grain の `nk`/`2026_nk` で行う(winner NLL は race-level・per-horse ID source は started-all が自然)。

| フィールド | 内容 |
|---|---|
| `race_subgroups` | race-level: 各 subgroup の `{winner_nll:{candidate,active,diff}, bootstrap_ci, n_races, cand_minus_uniform}` |
| `horse_subgroups` | horse-level: 各 subgroup の `{startedall_logloss:{candidate,active,diff}, bootstrap_ci, n_horses, cand_minus_uniform}` |
| `subgroup_guard` | **intersection-union**: critical(`2026_only`・`nk`・`2026_nk`)が**全て PASS**で pass |
| `subgroup_guard_reasons` | 各 critical の三値判定(PASS/FAIL/NO_DECISION)+ CI + cand−uniform |

- **三値判定**(codex C2): **grain 別 margin ε**(race-level winner NLL=`non_inferior_margin_winner_nll` 0.005 / horse-level per-horse logloss=`non_inferior_margin_horse_logloss` 0.001、後者は約5–10倍小さいスケールに合わせる、analyze A1)に対し PASS=CI 上限<ε / FAIL=CI 下限>ε / NO_DECISION=跨ぐ。adopted は critical 全 PASS 必須(NO_DECISION は非否決だが十分条件でもない)。
- **absolute 水準**(codex C6): 診断は subgroup 内 candidate − uniform(race-level=winner NLL の uniform=−log(1/N_started)、horse-level=started-all per-horse logloss の uniform=各 started 馬 win 確率 1/N の logloss、analyze U1)。「subgroup vs 全体」は使わない(頭数・難度差)。
- subgroup 割当(eval/subgroups.py): **呼び出し側が per-race/per-horse 属性を注入**(race_date.year・horse_id `nk:` prefix・厳密前観測数)、subgroups.py は band 割当・集計・gate 判定のみ(結果非参照、FR-004、codex C7)。overround/odds 品質監査は subgroups.py に置かない(coverage-audit へ)。

## 5. CoverageAudit(US2/SC-005, 監査出力 — coverage 帯は F02 の obs_count 依存、analyze I1)

| 次元 | 内容 |
|---|---|
| year × ID source(canonical/nk:) × coverage 帯 | 過去市場履歴 1走以上 / 3走以上 / 5走以上の率 |
| overround 分布 / 境界値率(1.0, 999.9, 0) / popularity と q-rank 不一致率 | provenance 品質の可視化(D7) |

特徴に流入しない。CLI 出力 / artifact のみ。

## 6. リーク境界(II)

- F02 列・subgroup CI・coverage 監査は特徴に戻さない。
- 対象レース自身のオッズ/人気/q/s は特徴に入らない(behavioral leak-guard: 今走・同日・未来のオッズ変更で不変、過去変更で変化)。
- subgroup 割当は属性のみ(結果非参照)。
- F02 は default 意思決定支援モデルに入らない(p⊥q leak-guard)。
