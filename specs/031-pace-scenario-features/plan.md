# Implementation Plan: 展開・ペース構成特徴 (Race Pace Scenario / Field Composition, 031)

**Branch**: `031-pace-scenario-features` | **Date**: 2026-06-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/031-pace-scenario-features/spec.md`

## Summary

§3 中コスト第1弾。023 `pace_features.build_pace_features` が各馬について算出済みの as-of 優勢脚質(`front_runner_rate`/`closer_rate`/`rel_corner_pos_avg`、対象レース日より前・同日除外・`merge_asof(allow_exact_matches=False)`)を、**今走 race_id 内で leave-one-out(自馬除外)集約** して、レースのペース構成と「自馬脚質×フィールド構成」の相互作用を表す 7 列を生成する。新モジュール `pace_scenario_features.py` は build_pace_features の **出力のみ** を入力に取り、生 result/今走列を一切読まない ⇒ リーク安全が構造的に保証される(他馬の **過去 as-of** のみ使用)。025 の単一 as-of 源に結線し materialize/in-memory/serving fallback で同一値。FEATURE_VERSION features-008→009。新ソース列なし(running_style/corner は 023 で既にロード済み)⇒ source_fingerprint 無改修。事前登録 bundle OOS(baseline=features-008 vs candidate=features-009)で採否を機械判定。

## Technical Context

**Language/Version**: Python 3.12 (features package, uv)

**Primary Dependencies**: pandas, numpy（既存）。新規依存なし。

**Storage**: PostgreSQL 16（read-only、新規読取列なし）。parquet feature store（025, artifacts 配下・非コミット）。

**Testing**: pytest（features/tests/unit）。correctness（leave-one-out 集計・相互作用・coverage・NaN 伝播）・leak-guard・materialize parity。

**Target Platform**: features ライブラリ（training/eval/serving が build_feature_matrix 経由で透過利用）。

**Project Type**: 単一 Python パッケージ拡張（features/）。スキーマ変更なし・新パッケージなし。

**Performance Goals**: 生成は 1 回（025 materialize に相乗り）。field 集約は per-race groupby の O(頭数) 集計で安価。serving は単一レース fallback（1 レース分）。

**Constraints**: bit パリティ非交渉（materialize==in-memory, float64 固定）。リーク境界を新設しない（他馬今走を読まない）。

**Scale/Scope**: 約 62k races / 883k entries（2007–現在）。新規列 7（pace_scenario group 1 つ）。

## Constitution Check

Constitution v1.0.0 ゲート:

- [x] **I. データ契約**: raceId 12桁・2007年以降・ID は既存 loader 経由（新規結合なし）。ラベル不変。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 本群は build_pace_features の as-of 出力(strictly-before・同日除外済み)のみを race 内集約。今走 race_results/result_status/finish_order/corner_orders/running_style を読まない(過去 as-of 経由のみ)。自馬 leave-one-out 除外、他馬も同日除外を継承。利用可能タイミング=PRE_ENTRY(出馬表時点で読める展開情報)、欠損=NULL(0埋め禁止)。leak-guard test(自馬今走/他馬今走/同日/未来 不変 + ソース grep)で担保。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: bundle 事前登録 walk-forward OOS(baseline=features-008 vs candidate=features-009)。primary=平均 win LogLoss 改善 かつ ECE 非悪化 + fold ガード(strict majority・worst-fold ECE 2e-3・worst-fold dLogLoss 5e-3)。数値を見てから列/閾値を変えない。ablation は diagnostic 専用。**PASS**
- [x] **IV. 確率整合性**: 特徴追加のみ。win→joint(009) 不変。LightGBM/binary・Unknown=NaN 維持。**PASS**
- [x] **V. 再現性・監査**: parquet は DB から決定論再生成。FEATURE_VERSION 009 に bump。新ソース列なしで source_fingerprint 無改修だが、生成経路に依らず同値(パリティ)。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし(migration head 0006 不変)・新テーブルなし。UI 非該当。features 内拡張のみ。**PASS**
- [x] **品質ゲート**: 設計に codex:codex-rescue の second opinion を取得済(leave-one-out 連続量・相互作用主役・0埋め禁止・coverage 列・bundle 事前登録)。差分と採用根拠を research.md に記録。**PASS**

**Gate 結果**: 全 PASS。違反なし(Complexity Tracking 不要)。

## Project Structure

### Documentation (this feature)

```text
specs/031-pace-scenario-features/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1（列定義・集計契約）
├── quickstart.md        # Phase 1（実 DB 検証手順）
├── contracts/
│   └── pace-scenario-features.md   # 列契約・集計契約・不変条件
├── checklists/requirements.md      # spec quality（PASS 済）
└── tasks.md             # /speckit-tasks 出力（本コマンドでは未作成）
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── pace_features.py          # 023（既存・再利用、改修なし）— per-horse as-of 脚質の唯一の源
├── pace_scenario_features.py # 031（新規）— build_pace_features 出力を race 内 leave-one-out 集約
├── registry.py               # 改修: pace_scenario group + FEATURE_VERSION features-009
├── materialize.py            # 改修: build_asof_features に pace_scenario ブロック結線
└── loader.py                 # 無改修（新ソース列なし、023 で running_style/corner ロード済み）

features/tests/unit/
├── test_pace_scenario_features.py   # 新規: leave-one-out 集計・相互作用・coverage・NaN・float64
├── test_pace_scenario_leak.py       # 新規: 自馬今走/他馬今走/同日/未来 不変 + grep
├── test_materialize_core.py         # 改修: features-008→009 リテラル、pace_scenario in materialized_columns
└── test_feature023_leak_guard.py    # 改修: FEATURE_VERSION リテラル 008→009

training/src/horseracing_training/cli.py  # 改修: feature-eval 既定 --drop-groups を pace_scenario に
```

**Structure Decision**: features パッケージ内の純追加。`pace_scenario_features.py` は `build_pace_features(frames)` を呼び、その per-horse 出力(front_runner_rate/closer_rate/rel_corner_pos_avg)を race 内で集約する **二次特徴**。生の DB 列を読まないため、リーク面・パリティ面のリスクが 023 の as-of 機構に閉じ込められる。

## 実装アプローチ（要点）

1. **own 値の取得**: `build_pace_features(frames)` を呼び per-(race,horse) の as-of `front_runner_rate`/`closer_rate`/`rel_corner_pos_avg` を得る(二重実装しない)。
2. **フィールド母集団**: entry_status==STARTED の馬を race のフィールドとする(pace_features の field_size 定義と一致)。
3. **leave-one-out 集約**(per race_id, 非 null のみ):
   - `field_front_rate_ex_self` = (Σ_j front − self) / (count_nonnull − [self 非 null])
   - `field_closer_rate_ex_self` 同様、`pace_imbalance_ex_self` = field_front − field_closer
   - `style_mismatch` = own.rel_corner_pos_avg − leave-one-out mean(rel_corner_pos_avg)
4. **相互作用**: `front_pressure` = own.front × field_front_ex_self、`closer_setup` = own.closer × field_front_ex_self。
5. **coverage**: `field_style_coverage` = nonnull(front_runner_rate) 馬数 / field_size。
6. **NaN 規律**: 他馬 0/全 null → ex_self は NaN。own が null → 相互作用 NaN。0 埋め一切なし。全列 float64。
7. **結線**: materialize の build_asof_features に pace_scenario ブロックを追加(in-memory builder・serving fallback と同一関数)。registry に group 登録・FEATURE_VERSION 009。

詳細な列契約・集計の正確な式・端数挙動は [contracts/pace-scenario-features.md](contracts/pace-scenario-features.md) と [data-model.md](data-model.md)。

## Complexity Tracking

違反なし(全ゲート PASS)。記載不要。
