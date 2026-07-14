# Phase 1 Data Model: 070 過去市場 F03/F04/F05

**スキーマ変更なし・migration なし**。全て features build の派生列(materialize parquet + in-memory 経路)。DB テーブルは不変。**列名は spec.md の受入シナリオを正本とする**(codex B1・data-model 独自命名は破棄)。

---

## Feature groups(registry, features-019)

FEATURE_VERSION: `features-018` → `features-019`(純加算)。**F05 は 2 群に分割**(codex B3・support は F02 依存 / residual は F04 依存を別 drop するため)。

| group | 列数 | ソース | 依存 | materialize |
|-------|------|--------|------|-------------|
| `pm_core_strength`(F02, 069) | 9 | odds | — | 既存 |
| `past_market`(058) | 4 | popularity | — | 既存(**削除しない**) |
| `pm_rank_robust`(F03) | 5 | popularity | — | additive |
| `pm_expectation_residual`(F04) | 6 | finish_order + F02 q | F02 q 共有 | additive |
| `pm_conditioned_support`(F05a) | 6(3 value + 3 軸別 count) | F02 s + surface/distband/venue | **F02 採用時のみ** | additive |
| `pm_conditioned_residual`(F05b) | 2(1 value + 1 count) | F04 finish_resid + surface | **F04 ADOPT 時のみ** | additive |

新ソース生列なし(popularity/finish_order/odds は既存 loader・fingerprint 内)→ **source_fingerprint 不変**。

---

## F03 pm_rank_robust(5列)

過去レースの市場人気 rank を percentile 化して strictly-before as-of 集約。**popularity-only complete-field**(codex 見落とし・odds 非依存の robust fallback が目的)。

| 列(spec 正本) | 定義 | 母集団 | NaN 条件 |
|----|------|--------|----------|
| `asof_pm_rankpct_last` | 直近1有効走の `u=1-(rank-1)/(N_started-1)` | complete-field 過去走 | obs<min |
| `asof_pm_rankpct_mean5` | 直近5有効走の u 平均 | 同上 | obs<min |
| `asof_pm_favorite_rate5` | 直近5走の `rank==1` 率 | 同上 | obs<min |
| `asof_pm_top3fav_rate5` | 直近5走の `rank<=3` 率 | 同上 | obs<min |
| `asof_pm_rank_obs_count` | complete-field かつ popularity 有効な過去走数(**F02 obs と別**) | 同上 | 0=許容(has_obs) |

- **complete-field**: started 全馬に valid **popularity**(odds ではない)があるレースのみ。
- **rank**: started 内 popularity 順。取消で raw popularity が started 頭数超過なら started 内で **competition re-rank**(行順非依存)。
- **tie**: competition rank(1,2,2,4)・horse_id/行順で捏造しない。
- **N=1**: u=1(観測に数える)。

**検証規則**: rankpct ∈ [0,1]・rate ∈ [0,1]・obs_count ≥ 0 整数・tie 決定論(入力行順シャッフル不変)。

---

## F04 pm_expectation_residual(6列)

「市場がどれだけ間違えたか」の符号付き残差。**2母集団分離**。分母は **N_started**(u と v で尺度統一・codex 見落とし)。

| 列(spec 正本) | 定義 | 母集団 | NaN 条件 |
|----|------|--------|----------|
| `asof_pm_finish_resid_mean5` | `e=v-u`(v=`1-(finish_order-1)/(N_started-1)`)直近5 | **finished** 過去走 | **finished_obs<min**(内部) |
| `asof_pm_finish_resid_career` | 同 all-prior 平均 | finished | **finished_obs<min**(内部) |
| `asof_pm_win_resid_mean10` | `w=I(win)-q`(q=F02 complete-field share)直近**10** | **started** 過去走 | started_obs<min |
| `asof_pm_win_resid_career` | 同 all-prior 平均 | started | started_obs<min |
| `asof_pm_resid_sd5` | **win_residual**(I(win)-q)の直近5走 sample sd(ddof=1・started 母集団・finish residual ではない) | started | obs<2 |
| `asof_pm_result_obs_count` | 過去 started 結果走数 | started | 0=許容 |

- finish は DNF/失格/取消を除外(finished のみ)、win は started 全馬(非勝利=0)=068 母集団。
- **NaN ゲート(2母集団・analyze U1)**: `finish_resid_*` は **finished 観測数 < min_obs=3 で NaN**(内部 finished カウント)、`win_resid_*` は started 数。surfaced `asof_pm_result_obs_count` は started 数の1本のみ(6列契約維持)。
- v の分母 = **N_started**(u と同じ・N_fin ではない)。
- q は F02 の primitive を共有(再計算しない)。
- **リーク**: 過去結果 × 過去市場(strictly-before)=対象レース非参照。

**検証規則**: e/w ∈ [-1,1]・win_resid の started 母集団が 068 win_realized と行一致・q 共有(独自再計算しない)。

---

## F05 pm_conditioned_support / pm_conditioned_residual(4 value 列 + 軸別 count: support 3+3、residual 1+1)

surface/distband/venue 条件別の階層縮約。**support(F02 s)と residual(F04)を別群**。

| 群 | 列(spec 正本) | 定義 | 依存 |
|----|------|------|------|
| support | `asof_pm_support_surface` | F02 s の surface 別 λ=5 縮約 | F02 採用 |
| support | `asof_pm_support_distband` | 同 distband 別 | F02 採用 |
| support | `asof_pm_support_venue` | 同 venue 別 | F02 採用 |
| support | `asof_pm_support_cond_count_{surface,distband,venue}` | 各軸の**実セル観測**数 | — |
| residual | `asof_pm_finish_resid_surface` | F04 finish_resid の surface 別縮約 | F04 ADOPT |
| residual | `asof_pm_finish_resid_surface_count` | surface 実セル観測数 | — |

- 縮約: `(n_cell·mu_cell + λ·mu_parent)/(n_cell+λ)`、λ=5。n_cell=0 は親 fallback。
- **as-of 手順(codex 論点1)**: target 時点で「最新 cell の累積 sum/count」と「target 直前の overall parent sum/count」を**別々に as-of 取得してから縮約**(縮約済み値を持ち越さない=親が陳腐化しない)。親は cumsum−当日=pool-end 非依存。
- **軸別 count**(codex B5): 単一 count では 3 軸 × support/finish の異なる母集団を表現不能 → 各出力に対応する count。valid count は実セルのみ(親fallback除外)。
- 列は常に build、依存は recipe/keep-drop matrix で制御(NOT_RUN)。二重条件(surface×dist)は初版で作らない。

**検証規則**: 軸別 valid_count は実セルのみ・親平均 cumsum−当日で pool-end 非依存(materialize parity)・distband は既存 bins。

---

## Keep/drop matrix(段階評価, gate-config.json に記録・operator 駆動)

各段の完全 recipe を contracts/cli.md に明示列挙(gate-config `staged_evaluation` は CLI 非消費のため記録用・codex 論点3)。

| 段 | candidate 群 | active(baseline)群 | 両arm drop |
|----|----------------|---------------|-----------|
| F03 置換 | base − `past_market` + `pm_rank_robust` | base(`past_market`, F03なし) | F04, F05a, F05b |
| F04 追加 | 現base + `pm_expectation_residual` | 現base | F05a, F05b |
| F05 support | 現base + `pm_conditioned_support` | 現base | `pm_conditioned_residual` |
| F05 residual(F04 ADOPT時のみ) | 現base + `pm_conditioned_residual` | 現base | — |
| stack-safety-check | 採用群合成 | lgbm-064系 | — |

`base` = **accuracy-first(lgbm-064-f02acc)base**(F02 `pm_core_strength` を keep=p⊥q の default lgbm-063 とは別物・「default 構成」ではない)。群名は `_expand_group_drops`(FEATURE_GROUPS 逆引き)で列名展開し両 arm 対称 drop。**最終 verdict = `gate.adopted AND subgroup_guard`(driver 適用)**。

---

## Serving compat(registry)

- `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-019"]` = `{"features-018": <lgbm-064 完全hash>, "features-017": <lgbm-063 完全hash>}`(**両直接 pin・非推移・metadata の完全 hash**・codex 論点4)。既存 059/061/069 履歴 entry は不変。
- **同一版で列 subset を drop した全 artifact が NOT_SERVABLE_PENDING_PROFILE**(F03 置換だけでなく未採用 F04/F05 を drop した最終 candidate も・codex 見落とし)。loader は same-version の global hash 完全一致のみ exact=recipe-drop subset を fail-closed 拒否。paired-eval は再fit で評価可・production 昇格スコープ外。
- byte-parity 検証: model-input ≈137列で lgbm-063/lgbm-064 compat-load。
