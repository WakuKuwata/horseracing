# Implementation Plan: ペース/時計シグナルの特徴量化 (Pace & Time Features)

**Branch**: `023-pace-time-features` | **Date**: 2026-06-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/023-pace-time-features/spec.md`

## Summary

既に ingest 済みの結果時データ（上がり3F・走破時計・着差・通過順位・脚質）を、**対象レースより前の馬の実績だけ**で as-of 集計したリーク安全な特徴に変換し、LightGBM win モデルに追加。生の時計は距離/馬場で水準が違うため**レース内相対化を主**に正規化してから集計。採用は 020 と同一の walk-forward OOS ゲート（PRIMARY=win LogLoss 改善 かつ ECE 非悪化 + strict majority fold + worst-fold LogLoss 上限 + 条件別差分）で baseline=features-005 を上回る時のみ。market_edge で市場超過も診断（SECONDARY）。スキーマ変更なし、features-006。

## Technical Context

**Language/Version**: Python 3.12（features/training/eval）

**Primary Dependencies**: pandas/numpy（as-of 集計・正規化）、LightGBM（既存 win モデル）、horseracing-eval（020 feature-eval/ablation/market_edge ハーネス、PREDICTOR-AGNOSTIC）、horseracing-features（registry/builder/loader）

**Storage**: PostgreSQL 16（read-only、既存 race_results/race_horses を読む）。**新規テーブル/カラムなし**。loader を拡張して finish_time/finish_time_diff/corner_orders（race_results）+ running_style（race_horses）を SELECT に追加（現状未ロード）。

**Testing**: pytest + testcontainers（features/training/eval）。leak-guard（今走結果・同走馬・同日・未来基準で不変）、cutoff、Unknown≠0、採用ゲート、ablation。

**Target Platform**: バッチ（CLI: 学習・評価）。

**Project Type**: ML ライブラリ拡張（features + training + eval）、web/UI なし。

**Performance Goals**: 既存 build_feature_matrix と同等オーダー（pandas as-of、62k races/883k entries で実用的）。

**Constraints**: リーク境界（憲法 II、result-time を as-of のみ）、評価先行（憲法 III）、確率整合性（IV、win→joint 維持）、スキーマ変更なし（VI）。

**Scale/Scope**: 2007–2024（62k races）。新特徴 1 主 group（pace_time）+ 1 任意 group（position_style）、計 ~6–10 本。

## Constitution Check

Constitution v1.0.0 ゲート:

- [x] **I. データ契約**: raceId 12桁・2007+・horse_id 結合は既存契約。馬番/枠番は ingest 修正済（c8cd98b）。ラベルは内部 win/top2/top3。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 全特徴は対象レースより前の馬実績のみ（`_cumulative_before`+`merge_asof(allow_exact_matches=False)`、同日除外）。result-time（time/上がり/順位/脚質）は今走分を使わない。正規化基準は過去のみ。leak-guard を **今走結果・同走馬今走値・同日他レース・未来年基準** まで拡張（FR-002/002a）。odds/結果は特徴にしない。各特徴に source/timing/missing 記載、Unknown≠0。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 候補事前固定（OOS で特徴選択しない）、walk-forward OOS、PRIMARY=平均 win LogLoss 改善 かつ ECE 非悪化 + strict majority fold + worst-fold LogLoss 上限 + 条件別差分。baseline=features-005。正規化方式は過去データで識別力確認後に確定。**PASS**
- [x] **IV. 確率整合性**: win→joint（009）維持、0≤1着率≤2着以内率≤3着以内率≤1。新特徴は win モデル入力のみで joint 派生に介入しない。**PASS**
- [x] **V. 再現性・監査**: model_versions に feature_version=features-006 を記録、決定論（seed 固定、020 同様）。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし（head 不変）。UI なし。020 の registry.FEATURE_GROUPS+評価ハーネスを再利用。**PASS**
- [x] **品質ゲート**: 新規 ML 特徴 spec として codex second opinion 実施済（spec「codex レビュー所見」、research R1–R6）。本 plan の正規化方式・採用ゲート補強も下記に根拠記録。**PASS**

スキーマ変更なし・違反なし → Complexity Tracking 不要。

## 主要設計判断（codex second opinion 反映）

1. **正規化（P0）= レース内相対化を主**。各過去レース内で「そのレースの平均/基準との差」を取る（リーク面が小さい）。条件別 z-score を併用する場合、平均/分散は **as-of（過去）分布のみ** から作り、少数条件は null/粗い条件にフォールバック。**着差（finish_time_diff）併用**で「強メンバー戦の相対不利」を緩和（codex P0/FR-006a/b）。
2. **リーク防止（P0）= 正規化済み過去走 row を先構築 → その row だけを as-of 集計**。loader 拡張（finish_time/finish_time_diff/corner_orders/running_style 追加）が最大の危険点なので、追加直後に leak-guard を拡張版（同走馬/同日/未来基準）で固める（FR-002a）。
3. **corner/style は MVP 主対象外・別 group**（position_style）として ablation で寄与確認、無ければ採用しない（codex P1）。MVP は pace_time group（レース内相対の上がり3F・走破時計・着差）。
4. **市場織り込みリスク（P1）**: 時計/上がりは最注目指標で市場 q に織り込み済みの公算 → 「LogLoss 微改善・市場超過ゼロ」も想定内。023 は小さく進め、市場超過は主目的にしない。届かなければ次候補（条件替わり/バイアス逆行、spec deferred）。
5. **採用ゲート補強（P0）**: 020 の `n_win*2>=n_folds`（偶数 fold で半数通過）を **strict majority** に。worst-fold LogLoss 悪化上限 + 条件別（距離帯/芝ダ/going/年/q帯）LogLoss/ECE 差分を採用レポートに追加（FR-011/011a）。

## Project Structure

### Documentation (this feature)

```text
specs/023-pace-time-features/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── feature_contract.md   # 新特徴の registry 契約 + 評価 CLI
└── tasks.md                  # /speckit-tasks で生成
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── loader.py            # 拡張: finish_time/finish_time_diff/corner_orders/running_style を SELECT 追加
├── pace_features.py     # NEW: レース内相対化 + as-of 集計（pace_time group）
├── position_features.py # NEW (任意): 相対通過順位・脚質分布（position_style group）
├── registry.py          # FEATURE_GROUPS に pace_time(+position_style) 追加、FEATURE_VERSION=features-006
└── builder.py           # 結線

eval/  : 020 の feature_eval/ablation/market_edge を再利用（条件別差分 + strict majority を harness/report に追加）
training/: predictor.drop_features（020 既存）で baseline=features-006 全 group drop

tests:
features/tests/ : leak-guard(今走/同走馬/同日/未来基準)・cutoff・正規化(条件差吸収)・Unknown≠0
eval/tests/     : 採用ゲート(strict majority/worst-fold/条件別)・ablation 分離
```

**Structure Decision**: features にペース/時計の as-of 特徴ビルダを新規追加（020 の extra_features/human_form と同じ層・機構）。eval/training は 020 ハーネスを再利用し、採用レポートに条件別差分と strict-majority を足すのみ。新規パッケージ・スキーマ変更なし。

## Complexity Tracking

> 憲法違反なし・スキーマ変更なしのため記載不要。
