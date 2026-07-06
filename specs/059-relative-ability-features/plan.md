# Implementation Plan: Within-race relative-ability features

**Branch**: `059-relative-ability-features` | **Date**: 2026-07-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/059-relative-ability-features/spec.md`

## Summary

既存の as-of 能力列を「対象レースの started フィールド内で leave-one-out 相対化」した新群
`relative_ability`(FEATURE_VERSION features-013→014)を追加する。**031 pace_scenario の能力版**:
031 は脚質を LOO 集約したが、本 feature は同じ機構を能力列(win_rate 等)に適用し、加えて中核
2 軸の field 内 percentile rank を足す。狙いは「木が per-row で拾えない within-race 文脈(相手
関係の中での相対的な格)」を特徴側で明示すること。

**技術アプローチ**: 新モジュール `relative_ability_features.py` を `build_asof_features`(025 単一
as-of 源、in-memory と materialized の両経路が経由)に 1 箇所結線。入力は同関数内で既にマージ済み
の as-of 能力列 + `frames`(started 判定)のみ。新ソース列なし → source_fingerprint 不変・
materialize-safe(per-race 決定的・pool-end 非依存、031 と同一性質)。migration なし・API/OpenAPI・
スキーマ・009 導出不変。採否は事前登録 feature-eval(spike 再現)+ **本番 pl_topk 再学習で
lgbm-056(0.21615)超え**を確認して確定。

## Technical Context

**Language/Version**: Python 3.12(uv workspace)

**Primary Dependencies**: pandas / numpy(within-race 集約)、LightGBM(pl_topk 目的関数)、
既存 `features` / `training` / `eval` パッケージ

**Storage**: PostgreSQL 16(read-only。新規テーブル・migration なし)/ parquet(025 materialize)

**Testing**: pytest + testcontainers(features unit + bit-parity + leak-guard; training/eval 回帰)

**Target Platform**: ローカル CLI / 手動運用(憲法 技術制約)

**Project Type**: single monorepo(ML パイプライン)。UI 変更なし

**Performance Goals**: build 段の追加コストは軽微(within-race groupby transform のみ)。
再学習は vectorized pl_topk + bulk eval loader で ~20 分/回

**Constraints**: bit-parity(materialize==in-memory, check_exact)・リーク境界不変・float64 固定・
NaN 維持(0 埋め禁止)

**Scale/Scope**: 全 950k+ 行 × 新 13 列(11 deviation + 2 rank、research D1 で確定)。18/19-fold walk-forward OOS

## Constitution Check

Constitution v1.0.0 ゲート:

- [x] **I. データ契約**: raceId 12桁・2007+・ID 結合なし・ラベル不変(特徴追加のみ)。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 新群は全て `source=relative`/`timing=PRE_ENTRY`/
  `missing=NULL` を registry に宣言。入力は strictly-before as-of 列のみ(他馬の今走結果・オッズ・
  同日値を一切参照しない=031 と同じ境界)。leak-guard test で「今走結果/オッズ/同日他馬改変 →
  新群不変」を機械固定。walk-forward 境界は既存ハーネスが適用。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 事前登録 `feature-eval --drop-groups relative_ability`
  (baseline=features-013)を採用ゲートに再実行。spike 実証(LogLoss −0.00118/AUC +0.00421/
  19-19)を spec に事前登録済み。加えて本番 pl_topk model-eval で lgbm-056 比較 + ECE。**PASS**
- [x] **IV. 確率整合性**: 009 win→joint・Unknown/取消除外・Σ 整合は不変(特徴を足すだけ、
  導出層・postprocess に変更なし)。**PASS**
- [x] **V. 再現性・監査**: FEATURE_VERSION features-014 を artifact/metrics に記録。materialize
  manifest の feature_version 更新。新群は特徴定義版で再現可能。**PASS**
- [x] **VI. feature 分割規律**: UI/API/DB 契約変更なし(features のみ)。migration なし。
  FEATURE_VERSION bump は特徴群追加の正当理由(026/030-033/041/056 前例)。**PASS**
- [x] **品質ゲート (codex)**: 初回起動はハングしたが analyze 中の codex が**完走し second opinion
  を取得**(research D7)。最重要指摘=「model-eval の pl_topk 検証は baseline が binary で feature
  価値を測れない」を採用し T012 を同一プロトコル train-evaluate 比較に是正。他指摘も docs 反映。
  両案差分・採用根拠を D7 に記録。**PASS**。

**結論**: 全原則 PASS。schema/API/probability 不変・リーク境界は 031 と同一構造。違反なし。

## Project Structure

### Documentation (this feature)

```text
specs/059-relative-ability-features/
├── plan.md              # This file
├── research.md          # Phase 0: design decisions + self-review (codex unavailable)
├── data-model.md        # Phase 1: feature columns / registry / materialize contract
├── quickstart.md        # Phase 1: how to build, gate, retrain, verify
└── checklists/
    └── requirements.md   # spec quality checklist
```

契約(contracts/)は N/A: API/DB/OpenAPI の外部インターフェース変更なし(features 内部のみ)。

### Source Code (repository root)

```text
features/src/horseracing_features/
├── relative_ability_features.py      # NEW: build_relative_ability_features (LOO rel + rank)
├── materialize.py                    # build_asof_features に 1 行結線(single as-of source)
└── registry.py                       # 新群 relative_ability 登録 + FEATURE_VERSION features-014

training/src/horseracing_training/
└── cli.py                            # 変更なし（既定 drop-group は不変。T003 の群登録で
                                      #   --drop-groups relative_ability が gcols 経由で解決）

features/tests/                       # NEW unit(LOO 意味論・rank・NaN)・bit-parity・leak-guard
training/tests/ · eval/tests/         # 回帰(列追加でのゲート再現)
```

**Structure Decision**: 031 pace_scenario と同一配置。新モジュール 1 個 + 既存 3 ファイルへ最小
結線。`build_asof_features` が in-memory / materialized 双方の唯一の as-of 源なので、結線は 1 箇所
で両経路に伝播(025 の drift 防止設計)。

## Complexity Tracking

Constitution 違反なし。追加の正当化不要。
