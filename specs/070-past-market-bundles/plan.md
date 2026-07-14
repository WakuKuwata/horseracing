# Implementation Plan: 過去市場 rank/residual/conditioned bundle(F03/F04/F05)

**Branch**: `070-past-market-bundles` | **Date**: 2026-07-14 | **Spec**: [spec.md](spec.md)

**Input**: 再制定書 F03/F04/F05、069 の F02 + subgroup ゲート基盤の上に構築

## Summary

069 の F02(pm_core_strength)+ 068/069 評価契約・subgroup ゲートの上に、過去市場の残り3 bundle を features-018→**019 純加算**で追加し、069 の subgroup 付き paired-eval で 1 bundle ずつ accuracy-first candidate として採否する。

- **F03 pm_rank_robust**(features/): rank percentile `u=1-(rank-1)/(N_started-1)` を **popularity-only complete-field** で strictly-before as-of 集約(5列 spec 正本: rankpct_last/mean5/favorite_rate5/top3fav_rate5 + rank_obs_count)。058 生 rank の **recipe 置換**候補(candidate は `past_market` 群 drop + F03、baseline は 058・F03 なし=帰属分離)。
- **F04 pm_expectation_residual**(features/): `finish_residual=v-u`(finished・v 分母 N_started)・`win_residual=I(win)-q`(started 非勝利=0・mean10)を strictly-before 集約(6列 spec 正本=finish_resid_mean5/career・win_resid_mean10/career・resid_sd5・result_obs_count を含む)。additive。F02 の q・F03 の u primitive を共有。
- **F05 pm_conditioned_support / pm_conditioned_residual**(features/): surface/distband/venue 条件別 λ=5 階層縮約。**2 registry 群**(support←F02, residual←F04 を別 drop するため・codex B3)+ 軸別 valid count。

**新ソース列なし**(popularity/finish_order は既存 loader・q は F02 の odds 由来)→ source_fingerprint 不変・materialize-safe(059/061 同型、069 と違い odds 追加不要)。評価は 069 の `paired-eval --subgroups` を再利用するが **最終 verdict = `gate.adopted AND subgroup_guard`(driver 適用)**(paired_eval は両者を別々に返す・codex B2)、`eval_window` は CLI `--from/--to` で渡す。training は 069 の `drop=group` 展開 + `--register-candidate` を流用し、per-arm keep/drop matrix で段階評価(gate-config は記録・operator が完全 recipe を実行)。

**スキーマ・API・OpenAPI・migration 不変**。変更は `features/`(3 新モジュール=**4 群**[F05 を 2 群]・registry 019 純加算・compat 018/017 両直接 pin[完全 hash]・materialize 結線・q/s/N と u の公開 primitive)、`training/`(段階評価 operator 手順)。obs_count 配線(live coverage 帯)は任意。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: numpy, pandas, lightgbm, scikit-learn(既存)。新規依存なし。

**Storage**: PostgreSQL 16(read-only)。**スキーマ変更なし・migration なし**。F03=popularity(既存)、F04=finish_order(既存)+ F02 q、F05=F02 s / F04 residual を条件別。**新ソース列を読まない → source_fingerprint 不変**(069 は odds 追加で fingerprint 拡張したが 070 は不要)。

**Testing**: pytest + testcontainers。合成データで F03 tie/gap・F04 2母集団・F05 階層縮約/valid count・leak-guard を固定、実 DB で features-019 の共有137列 byte-parity + lgbm-064/063 compat-load + 各 bundle の subgroup 付き paired-eval を検証。

**Target Platform**: features build + eval CLI(069 paired-eval 再利用)+ training(candidate 学習・段階評価)。

**Project Type**: ML 特徴量 + 評価(web/UI なし)。

**Performance Goals**: F03/F04/F05 は 069 F02 と同型の as-of(per-row、pool-end 非依存)で materialize-safe。F05 階層縮約も per-row の親 fallback(全体集約は cumsum−当日、pool-end 非依存)。

**Constraints**: 憲法II(対象レース市場/結果 非入力・strictly-before + 同日除外・部分 field 再正規化禁止・列名トークン回避 + behavioral leak-guard・default 非組込・過去 q/結果のみ)、III(1 bundle・OOS 後列選別禁止・058 rank と F03 同時変更しない・per-arm keep/drop matrix・式/λ/tie OOS 前固定)、VI(スキーマ/API 不変・FEATURE_VERSION bump は additive + compat 018/017 両 pin)。

**Scale/Scope**: 957,355 race_horses 行。2007–2026。lgbm-064-f02acc(features-018 candidate)/ lgbm-063(features-017 active)を compat 維持。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: PASS。raceId・年範囲・id_mappings・ラベル定義不変。F03/F04/F05 は既存 popularity/finish_order/odds を読むのみ、新結合なし。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS。全 bundle は strictly-before + 同日除外、対象レース市場/結果 非流入(behavioral leak-guard)、部分 field 再正規化禁止(F04 win_residual は F02 complete-field 継承)、列名トークン回避(`asof_pm_*`)、default 非組込。**F04 の `win_residual=I(win)-q` は過去レースの q(strictly-before)でリークでない**(codex #2)。`p⊥q` でなく **「対象レース市場非入力」**(codex #6・lgbm-063 は 058 列を含む)。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS。1 bundle ずつ事前登録・OOS 後列選別しない・058 rank と F03 同時変更しない(帰属)・**per-arm keep/drop matrix**(対象外群を両 arm から drop、F03 評価に F04/F05 混入させない)・式/λ/tie を gate-config に OOS 前固定・069 subgroup ゲート採否。
- [x] **IV. 確率整合性**: PASS。win 特徴で 009 の Σ=1・順位保存に影響しない(069 同経路)。
- [x] **V. 再現性・監査**: PASS。coverage・subgroup CI・bundle 事前登録・feature_hash pin(018+017)を記録。
- [x] **VI. feature 分割規律**: PASS。スキーマ・API・OpenAPI・migration 不変。FEATURE_VERSION 018→019 bump は **schema 純加算**(058 残す)+ **compat 018/017 両 pin**(推移しない、codex #4)で serving 不変。**F03 置換 candidate 自身は serve 不能=`NOT_SERVABLE_PENDING_PROFILE`・production 昇格スコープ外**(codex #5)。
- [x] **品質ゲート**: PASS。codex second-opinion を spec フェーズ(6指摘全採用)+ plan フェーズ(**verdict=REQUEST CHANGES・全ブロッカー反映**=列契約を spec 正本に再整合 B1・verdict は driver AND B2・F05 2 群 B3・CLI 契約修正 B4・軸別 count B5・共有 primitive・stack-safety 改称・完全 hash pin・popularity-only・N_started・NOT_SERVABLE 拡張)で取得。採否は research D7 に記録。tasks フェーズで再レビュー予定。

**判定**: NON-NEGOTIABLE(II/III)含む全ゲート PASS。ブロッキング違反なし → Phase 0 へ。

## Project Structure

### Documentation (this feature)

```text
specs/070-past-market-bundles/
├── plan.md              # This file
├── research.md          # Phase 0: F03 tie/gap・F04 2母集団・F05 階層縮約・compat・段階評価・codex D
├── data-model.md        # Phase 1: RankRobust / ExpectationResidual / Conditioned 列 + keep/drop matrix
├── contracts/
│   └── cli.md           # Phase 1: 段階評価(per-bundle paired-eval)+ register-candidate CLI
├── gate-config.json     # F03/F04/F05 式パラメータ + keep/drop matrix + subgroup 閾値(OOS前固定)
├── quickstart.md        # Phase 1: features-019 parity + compat + 各 bundle 採否手順
└── tasks.md             # Phase 2: /speckit-tasks が生成
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── pm_rank_robust.py       # [NEW] F03: rank percentile u as-of(popularity-only complete-field)
├── pm_expectation_residual.py # [NEW] F04: finish/win residual(N_started 分母, 2母集団, F02 q/F03 u 共有)
├── pm_conditioned.py       # [NEW] F05: 条件別 λ=5 縮約 → 2 群(support / residual)+ 軸別 count
├── pm_core_strength.py     # [EDIT] q/s/N を返す公開 primitive を追加(F04/F05 共有・現状は s のみ返す)
├── past_market_features.py # [READ] 058 rank(F03 置換対象・削除しない)
├── registry.py             # [EDIT] FEATURE_VERSION 018→019・4 群(F05×2)・compat 018/017 両直接 pin(完全 hash)
├── materialize.py          # [EDIT] build_asof に F03/F04/F05 additive left-merge(source_fingerprint 不変)
└── loader.py               # [READ] popularity/finish_order/odds 既存(新ソース列なし)

eval/src/horseracing_eval/
├── paired.py               # [READ] 069 paired-eval を再利用(gate と subgroups を別々に返す=verdict は driver で AND)
└── subgroups.py            # [READ] 069 subgroup 割当を再利用

training/src/horseracing_training/
├── recipe.py               # [READ] ModelRecipe.drop_features(069)を per-arm keep/drop に流用
└── cli.py                  # [READ / 任意 EDIT] 段階評価は完全 recipe を operator が実行(gate-config 記録)。live coverage 帯が要れば obs_count 配線=任意

features/tests, training/tests, serving/tests  # [NEW] F03/F04/F05 数式・leak・parity・compat・段階評価 matrix
# eval/ は reuse-only(paired.py/subgroups.py 変更なし)=新 eval テストなし
```

**Structure Decision**: 069 の基盤を最大限再利用。**3 bundle は `features/`**(F02 と同層・独立モジュール・registry に **4 独立 group**=F05 を support/residual に分割 codex B3)。**評価は 069 の `eval/paired.py --subgroups` を再利用**するが、**paired_eval は gate と subgroups を別フィールドで返すため最終 verdict = `gate.adopted AND subgroup_guard` を driver/operator が適用**(069 と同じ read-time AND・codex B2 で「eval 変更なし」を是正)。**段階評価は gate-config を記録・operator が contracts の完全 recipe を順に実行**(`staged_evaluation` は CLI 非消費)。共有 primitive(q/s/N・u)を pm_core_strength / pm_rank_robust に公開。materialize-safe(新ソース列なし=source_fingerprint 不変)。新パッケージ・新 eval 機構は作らない。

## Complexity Tracking

> ブロッキング違反なし。表は空。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| （なし） | — | — |
