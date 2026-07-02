# Implementation Plan: コーナー通過順の軌跡特徴 (041)

**Branch**: `041-corner-trajectory` | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/041-corner-trajectory/spec.md`

## Summary

過去走の通過順デルタ(直線の伸び late_gain・捲り mid_move・先行位置 early_pos)を as-of 集約した 4 列(`asof_late_gain_avg/best`・`asof_early_pos_avg`・`asof_mid_move_avg`)を features-012 として追加。023 pace の確立済み機構(runs 構築・field_size 正規化・merge_asof(backward, allow_exact_matches=False) = strictly-before+同日除外)を流用し、新モジュール `corner_trajectory_features.py` を 025 build_asof_features に単一経路で結線。**新ソース列なし = source_fingerprint 無改修**。採否は事前登録 18-fold OOS(drop-groups corner_trajectory)。spike(2019+ 3fold)で winner-NLL −0.0035・全 fold 改善を確認済み。

**codex 方針**: 本 feature は codex ブレスト #2 候補そのもので、落とし穴(今走 corner 禁止・過去走 as-of・頭数正規化・コーナー数差処理)は spec に織り込み済み。plan は 020-033 確立パターンの機械的反映のため再 second opinion は省略(CLAUDE.md「使わない場面」該当)。

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: pandas/numpy(既存)。新依存なし
**Storage**: PostgreSQL 16 read-only(スキーマ変更なし)。materialize parquet(025)
**Testing**: pytest(features unit、DB-free make_frames)+ 実 DB parity/feature-eval
**Target Platform**: 既存 features パッケージ拡張
**Project Type**: feature-module 追加(020-033 同型)
**Performance Goals**: corner_orders parse は 917k 行 O(n)(spike 実測で全体数分内)
**Constraints**: 憲法 II(strictly-before+同日除外・今走非参照)/III(事前登録 OOS)/V(materialize bit パリティ)
**Scale/Scope**: 4 新列、FEATURE_VERSION 011→012、モジュール 1 + registry + materialize 結線 + テスト 3 種

## Constitution Check

- [x] **I. データ契約**: raceId/期間/ID 結合に変更なし(PASS)
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 全列 source=race_results(過去走)+ PRE_ENTRY + NULL を registry 宣言。対象レースの corner/finish は merge_asof(allow_exact_matches=False)で構造的に排除(023 と同一機構)。finish_order の過去走ラベル利用は history/023 と同じ既存境界。leak-guard test(今走変更・同日変更・未来変更で不変 + grep)(PASS)
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 事前登録 18-fold feature-eval(--drop-groups corner_trajectory)、閾値は 030-033 と同一。spike は feasibility 根拠であり採否は本評価(PASS)
- [x] **IV. 確率整合性**: 特徴追加のみ。009/PL/Harville 不変(PASS)
- [x] **V. 再現性・監査**: FEATURE_VERSION bump、materialize bit パリティ、source_fingerprint 無改修確認(PASS)
- [x] **VI. feature 分割規律**: スキーマ変更なし・API/front 不変(PASS)
- [x] **品質ゲート**: codex はブレスト段階で本案を提案・落とし穴指摘済み(spec 織り込み)。plan は確立パターンの機械反映のため再意見省略を宣言(PASS)

**Gate result: PASS**

## Design Decisions

### D1: 生スコア算出(runs 構築)
- 023 `_pace_runs` と同型の runs 構築: race_results(finished + finish_order/corner_orders あり)× race_horses(started)× races(race_date)。
- parse: corner_orders(数値文字列 list)→ int list。失敗/空 → その走の軌跡 NaN。
- field_size = 当該過去走の started 数(023 と同じ groupby sum)。
- `late_gain = (corner_last − finish_order)/field_size`、`early_pos = corner_first/field_size`、`mid_move = max(pos[j]−pos[j+1])/field_size`(連続ペアなし=コーナー1つ → NaN)。field_size<=0 → NaN。

### D2: as-of 集約(spike の shift(1) を production 機構に厳密化)
- **expanding**(全過去走)集約: avg 3 本 + late_gain の cummax(best)。023 の `_rolling_asof` は recent-N rolling なので流用せず、同ファイル内に expanding 版ヘルパを追加(sort→groupby cumsum/cumcount/cummax)→ per-run 値を **merge_asof(on=race_date, by=horse_id, direction=backward, allow_exact_matches=False)** で対象行へ(strictly-before+同日除外を 023 と同一機構で担保)。
- NaN 伝播: 過去走に有効軌跡が 1 つもなければ NaN(0 埋め禁止)。

### D3: registry / version
- 4 列を source="pace"(過去走由来、023 と同区分)・PRE_ENTRY・NULL、group=`corner_trajectory`。STATIC_COLUMNS に入れない(= materialized_columns 自動収録)。
- FEATURE_VERSION "features-011"→"features-012"。**版リテラル波及**: test_feature023_leak_guard.py(040 マージ後 main 基準で該当箇所を 012 に)。

### D4: materialize 結線
- `build_asof_features` に corner_trajectory ブロックを単一経路で merge(031/032/033 と同型)。
- 新ソース列なし(corner_orders/finish_order/result_status は 023 で、race_horses/races は当初からロード&fingerprint 包含)→ source_fingerprint 無改修。fingerprint の list 列(corner_orders)対応は 025 で実証済み。
- serving 未来レース: has_future_rows → 単一レース fallback(生成と同一実装、既存機構)。

### D5: 採用判定
- `feature-eval --drop-groups corner_trajectory`(baseline=features-011 相当、candidate=features-012)。**注意: feature-eval の学習は binary 経路**(eval は predictor-agnostic、既定 predictor 構成)だが、production は cond_logit。採否ゲートは従来どおり feature-eval(一貫性=030-033 と同一基準)で行い、**採用時の lgbm-041 学習は cond_logit+TE+isotonic(現行 production 構成)**。spike は cond_logit 経路で信号確認済みなので両経路の整合は期待できる。feature-eval に `--objective` はないため、TE/isotonic/cond_logit 込みの最終確認は train-evaluate の OOS サマリで担保。
- 採用: lgbm-041 active・lgbm-039 retired。不採用: ブランチ保全。

## Project Structure

```text
specs/041-corner-trajectory/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/corner-trajectory-features.md
└── tasks.md (Phase 2)

features/src/horseracing_features/
├── corner_trajectory_features.py   # 新: CORNER_TRAJECTORY_COLUMNS(4) + build_corner_trajectory_features
├── registry.py                     # 4 列登録 + FEATURE_VERSION=features-012
└── materialize.py                  # build_asof_features 結線

features/tests/unit/
├── test_corner_trajectory_features.py  # 値の正しさ/エッジ
├── test_corner_trajectory_leak.py      # leak-guard(今走/同日/未来 不変 + grep)
└── test_materialize_core.py            # INV-P1/P2/P3 拡張(4 列 + features-012)

training/src/horseracing_training/cli.py # feature-eval 既定 drop-groups を corner_trajectory に
```

## 実装フェーズ概要
1. Foundational: モジュール骨格 + registry/version(+リテラル波及)
2. US1: 生スコア + as-of 集約 + 値テスト
3. US2: leak-guard テスト
4. US3: materialize 結線 + parity テスト
5. US4: 実 DB 18-fold feature-eval → 採否 → (採用時)lgbm-041 train-evaluate → serving 確認
6. Polish: lint/テスト緑・CLAUDE.md・memory

## Complexity Tracking
新依存なし・スキーマ変更なし・確立パターン(031-033)踏襲。複雑度増分は corner parse と expanding as-of ヘルパのみ。
