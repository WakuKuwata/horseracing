# Implementation Plan: 過去オッズ量特徴(F02)+ subgroup ゲート拡張

**Branch**: `069-past-odds-features` | **Date**: 2026-07-13 | **Spec**: [spec.md](spec.md)

**Input**: 提案書 Phase 3 + 再制定書 F02、068 評価契約の上に構築

## Summary

068 の paired-eval を **subgroup(2026-only / canonical・nk: / coverage 帯)CI ゲート**へ拡張し(US1)、その物差しの上で **過去オッズ量特徴 F02 pm_core_strength**(`s=log(q×N)`、strictly-before as-of)を新 bundle・features-018 純加算・accuracy-first candidate として評価する(US2)。

2レイヤ:

1. **subgroup ゲート拡張(US1, eval/)**: `paired.py` に per-race 損失差の subgroup 集計(2026-only・ID source・過去市場 coverage 帯)+ 各 subgroup の開催日 block bootstrap CI + subgroup ガード(重要 subgroup 非悪化)を追加。coverage 監査(年×source×帯)を別途出力。068 の既存ゲートは不変で加算。

2. **F02 bundle(US2, features/ + training/)**: `pm_core_strength.py`(新モジュール)で過去 started 行のオッズ → race 単位 q → `s=log(q×N)` → 馬単位 as-of 集約(058 idiom)。registry に独立 group `pm_core_strength` + FEATURE_VERSION features-017→018 + compat map に features-017 hash(`300b28a9…`)を pin(lgbm-063 serving 不変)。058 rank 4列は削除しない。accuracy-first candidate モデルで US1 拡張ゲート評価。

**スキーマ・API・OpenAPI・migration 不変**。変更は `features/`(F02 + registry)・`eval/`(paired subgroup)・`training/`(candidate recipe / CLI)。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: numpy, pandas, lightgbm, scikit-learn(既存)。新規依存なし。

**Storage**: PostgreSQL 16(read-only)。**スキーマ変更なし・migration なし**(odds 列は既存)。ただし features loader は現状 popularity のみ読み **odds を読まない** → loader に `RaceHorse.odds` を追加(新ソース列)し source_fingerprint を odds 込みに拡張(056 前例、codex C4)。

**Testing**: pytest + testcontainers。合成データで F02 の q/s 数式・leak-guard・欠損を固定、実 DB で features-018 の共有列 byte-parity + lgbm-063 compat-load + subgroup 監査を検証。

**Target Platform**: features build(materialize/in-memory)+ eval CLI(paired-eval 拡張)+ training(candidate 学習)。

**Project Type**: ML 特徴量 + 評価(web/UI なし)。

**Performance Goals**: F02 は 058 と同型の as-of(per-horse、pool-end 非依存)で materialize-safe。subgroup 集計は paired-eval の per-race 損失差を再グループ化するだけで追加コスト無視可能。

**Constraints**: 憲法II(対象レース市場非入力・strictly-before + 同日除外・部分 field 再正規化禁止・列名トークン回避 + behavioral leak-guard・F02 を default に入れない p⊥q・subgroup 割当は属性のみ)、III(1 bundle・OOS 後列選別禁止・058 と同時変更しない・閾値/式 OOS 前固定)、V(coverage 監査・subgroup CI)、VI(スキーマ/API 不変・FEATURE_VERSION bump は compat 正当化)。

**Scale/Scope**: 957,355 race_horses 行(オッズ 99.6%)。2007–2026。2026 started 行の 36.8% が nk: ID(subgroup 監査対象)。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: PASS。raceId・年範囲・id_mappings・ラベル定義不変。F02 は既存 race_horses.odds 列を loader に追加して読む(新ソース列・migration 不要)、新結合なし。ID source(nk:)判定は horse_id prefix(既存)。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS。F02 は strictly-before(merge_asof allow_exact_matches=False)+ 同日除外、対象レース/同日/未来のオッズ非流入(behavioral leak-guard)、部分 field の q 再正規化禁止、列名は odds/popularity トークン回避(`asof_pm_*`)、default モデルに入れない(p⊥q)。subgroup 割当は race 属性 + horse_id prefix + 厳密前観測数のみ(結果非参照)。評価派生値(subgroup CI)を特徴に戻さない。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS。F02 は1事前登録 bundle・OOS 後に列選別しない。subgroup 閾値/式(recent-K/trend/sd5/q complete-field)を OOS 前固定。058 rank は同時変更しない(帰属分離)。採否は US1 拡張ゲート(winner NLL + 2026/nk: subgroup 非悪化 + top2/3 + 校正)。
- [x] **IV. 確率整合性**: PASS。F02 は win 特徴で 009 の Σ=1・順位保存に影響しない(068 と同経路・probability engine 不変)。**Σp≈1 回帰 assert を T021 に追加**して exemption でなくテストで閉じる(analyze C1)。
- [x] **V. 再現性・監査**: PASS。coverage 監査(年×source×帯)・subgroup CI・bundle 事前登録・feature_hash pin を記録。
- [x] **VI. feature 分割規律**: PASS。スキーマ・API・OpenAPI・migration 不変。FEATURE_VERSION 017→018 bump は F02 純加算 + compat pin(features-017 hash `300b28a9…`)+ 共有128列 byte-parity で serving 不変を担保。
- [x] **品質ゲート**: PASS。codex second-opinion を親から `codex exec` 直叩きで取得済み([codex-env-recovery]・spec フェーズ)。**主要指摘を全採用**(068ゲート不十分→US1 subgroup拡張 / 058同時削除禁止→独立group・rank保持 / provenance blocker→candidate限定 / features-018互換→legacy列非削除 + hash pin / 未定義詳細→FR-011 で固定)。plan フェーズで再レビュー(research D で採否記録)。

**判定**: NON-NEGOTIABLE(II/III)含む全ゲート PASS。ブロッキング違反なし → Phase 0 へ。

## Project Structure

### Documentation (this feature)

```text
specs/069-past-odds-features/
├── plan.md              # This file
├── research.md          # Phase 0: q/s 設計・recent-K/trend/sd5・subgroup ゲート・provenance・codex D
├── data-model.md        # Phase 1: PastMarketSupport(F02列) / SubgroupGateResult / coverage 監査
├── contracts/
│   └── cli.md           # Phase 1: paired-eval subgroup 拡張 + F02 採否経路(paired-eval)+ coverage-audit CLI
├── gate-config.json     # subgroup 閾値・recent-K・式の事前登録(OOS前固定, III)
├── quickstart.md        # Phase 1: SC-002(features-018 parity + lgbm-063 compat)/ SC-005(coverage監査)
└── tasks.md             # Phase 2: /speckit-tasks が生成
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── pm_core_strength.py  # [NEW] F02: 過去 started 行 → race q → s=log(q×N) → 馬 as-of 集約(058 idiom)
├── past_market_features.py # [READ] 058 rank bundle(拡張元・削除しない)
├── registry.py          # [EDIT] FEATURE_VERSION 017→018・group `pm_core_strength`・compat pin(017 hash)
├── materialize.py       # [EDIT] build_asof に F02 ブロック結線(single as-of 源、025)
└── loader.py            # [EDIT] RaceHorse.odds を追加(新ソース列)+ source_fingerprint 拡張(056同型, codex C4)

eval/src/horseracing_eval/
├── paired.py            # [EDIT] subgroup(2026/source/coverage帯)損失差 + subgroup CI + subgroup ガード
├── subgroups.py         # [NEW] subgroup 割当(属性のみ・結果非参照)。coverage 監査は含めない(training/ T020, codex C7)
└── bootstrap.py         # [READ] 068 block bootstrap を subgroup 別に再利用

training/src/horseracing_training/
├── recipe.py            # [EDIT] accuracy-first candidate recipe(F02 込み features-018)
└── cli.py               # [EDIT] paired-eval に --subgroups 出力 + F02 は paired-eval 経路で採否(feature-eval 不使用, codex C5)・coverage-audit 追加

features/tests, eval/tests, training/tests  # [NEW] q/s数式・leak-guard・parity・subgroup・coverage
serving/tests/                              # [NEW] lgbm-063 compat-load 検証(test-only、serving src 変更なし)
```

**Structure Decision**: 既存パッケージ境界を維持。**F02 特徴は `features/`**(058 と同じ層・新モジュール `pm_core_strength.py`、registry に独立 group)。**subgroup ゲート拡張は `eval/`**(068 paired.py の拡張 + 新 `subgroups.py`、predictor-agnostic 維持)。**candidate 学習は `training/`**。**`serving/` は test-only**(features-018 registry 下で lgbm-063 の compat-load を検証、src 変更なし=058 案C' の per-model feature_hash 互換を再確認)。新パッケージは作らない(058/068 の薄い増分)。eval→training 非依存を維持(subgroup 割当は eval 内で注入属性のみ使用、068 の PredictorFactory 注入は不変)。

## Complexity Tracking

> ブロッキング違反なし。表は空。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| （なし） | — | — |
