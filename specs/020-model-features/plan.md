# Implementation Plan: モデル改善 — リーク安全な特徴量拡張と walk-forward 採用ゲート

**Branch**: `020-model-features` | **Date**: 2026-06-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/020-model-features/spec.md`

## Summary

`features/` に新規リーク安全特徴量（recent_form / aptitude / race_condition / human_form の 9 特徴）を追加し、
`eval/`+`training/` を **fold 内選択の walk-forward 採用ハーネス**に拡張する。新特徴は既存の実証済みリーク機構
（`history._cumulative_before` の daily `cumsum − 当日分` = 厳密前 + 同日除外、`merge_asof(backward, exact 無し)`）
を転用し、跨馬統計（jockey/trainer）も jockey_id/trainer_id でグルーピングして daily-minus-current＝対象行+同日
除外を満たす。採用は **LogLoss 改善 かつ ECE 非悪化（PRIMARY）+ fold 別差分（勝ち fold/最悪 fold/ECE 差）+
group ablation + 過学習検査**。pseudo-ROI/Kelly と市場 q edge は SECONDARY diagnostic。成功基準は OOS win 改善
（市場超過は努力目標）。スキーマ変更なし（feature_version=features-005）。codex top-3 + 035/036 前例回避を機構化。

## Technical Context

**Language/Version**: Python 3.12（uv）。LightGBM / binary objective 維持。

**Primary Dependencies**: `horseracing-features`(registry/history 拡張) / `horseracing-training`(LightGBM、正則化
レンジ・fold 内選択) / `horseracing-eval`(walk-forward + 採用レポート + ablation + 市場 edge) / `horseracing-db`。
diagnostic で betting(011/016)・probability(010 q)。numpy/pandas/scikit-learn/lightgbm。

**Storage**: PostgreSQL 16。読: races/race_horses/race_results（履歴・特徴）。書: model_versions(feature_version)、
prediction/eval は既存経路。**スキーマ変更なし**（head 0006）。

**Testing**: pytest + testcontainers。cutoff テスト（当日以降変更で特徴不変）・target-row 除外テスト（跨馬統計）・
fold 内選択（選択リーク無し）・採用ゲート（LogLoss+ECE+fold 別差）・group ablation・決定論・leak-guard（odds/結果
非特徴）。

**Target Platform**: 手動 CLI（再学習・評価）。

**Project Type**: 既存 features/training/eval の拡張（新パッケージなし）。

**Performance Goals**: walk-forward × fold 内 inner split で学習回数増（許容、手動実行）。特徴は as-of 集計で O(N log N)。

**Constraints**: 全特徴 as-of/out-of-fold・同日除外・跨馬は対象行除外。選択も fold 内。market odds/結果は特徴に
しない。win→joint(009) 維持。特徴数上限・正則化レンジ固定。決定論（seed）。importance 単独で採否を決めない。

**Scale/Scope**: 9 新特徴（4 group）+ fold 内選択ハーネス + 採用ゲート + diagnostic。ranking/monotonic/model
family/multi-output/pedigree/未取得データは deferred。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート（ML feature のため II/III/IV が中心）:

- [x] **I. データ契約**: race_id/ラベル不変。新 ID なし。既存テーブル。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 全特徴 as-of/out-of-fold + 同日除外（既存 `_cumulative_before`/
  `merge_asof` 転用）。跨馬は jockey/trainer daily-minus-current＝対象行+同日除外。**特徴量選択・ハイパラも
  fold 学習窓内**（選択リーク無し）。market odds/結果は特徴にしない（leak-guard test）。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: walk-forward OOS で baseline 比較、採用は LogLoss 改善 かつ ECE 非悪化
  + fold 別差分（勝ち/最悪/ECE 差）。group ablation。評価ハーネスを学習選択より先に整備。**PASS**
- [x] **IV. 確率整合性**: win→joint(009) 維持、Unknown 欠損（0 代入しない）、新馬も出走頭数に含む。**PASS**
- [x] **V. 再現性・監査**: feature_version=features-005 を model_versions に記録、決定論（seed）。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし。ranking/multi-output/pedigree を将来に明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue`（ML 独立検証）second opinion を取得・記録（下表）。035/036 前例回避。**PASS**

### Second Opinion 記録（codex:codex-rescue — ML 設計レビュー）

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **A. 跨馬リーク** | jockey/trainer は target-encoding リーク最大。対象行+同日+out-of-fold 除外を仕様+テストで固定 | jockey_id/trainer_id daily `cumsum−当日`＝対象行+同日除外 + walk-forward 前のみ。target-row 除外テスト（R2/FR-003） |
| **B. 選択リーク** | 特徴/ハイパラ選択を各 fold 学習窓内で完結、全期間選択は OOS 汚染 | 候補特徴を**事前固定**（OOS で特徴選択しない）、fold 内はハイパラ/early-stopping のみ、評価＝デプロイ一致（R4/FR-005、analyze F1 解消） |
| **C. 採用指標** | LogLoss+ECE を primary、AUC は順位限定、pseudo-ROI は高分散 secondary | PRIMARY=LogLoss 改善 かつ ECE 非悪化、pseudo-ROI/Kelly は SECONDARY（R5/FR-006/010） |
| **D. 効率市場** | 公開情報は織り込み済み、win 改善≠市場超過。成功基準を win 品質に | 成功=OOS win 改善、市場超過は努力目標・diagnostic（R6/FR-011） |
| **E. 過学習** | 特徴数上限・正則化レンジ固定・fold 安定性、importance 単独不可 | 事前固定レンジ + gain/SHAP/ablation 符号・順位安定性（R5/FR-009） |
| **F. 035/036 前例** | 片側 fold + 校正未確認の false positive。fold 別差分・group ablation | fold 別差分（勝ち/最悪/ECE 差）+ group ablation を採用ゲートに（R5/FR-007/008） |
| **G. スコープ** | 履歴共有 group は ablation 必須、feature spec table + cutoff test 必須 | group 分離 ablation、registry FeatureMeta + cutoff/target-row test（R1/R3） |

最重要リスク TOP3: ①跨馬 target-encoding リーク ②選択リーク/評価=デプロイ不一致 ③偶然 fold/校正悪化の false
positive。①=daily-minus-current（対象行+同日除外）、②=候補特徴を事前固定（OOS で選択しない、fold 内はハイパラ
のみ、評価＝デプロイ一致）、③=LogLoss+ECE+fold 別差分 で対応。

## Project Structure

### Documentation (this feature)

```text
specs/020-model-features/
├── plan.md / research.md (R1-R7) / data-model.md / quickstart.md
├── contracts/feature_eval.md
├── checklists/requirements.md (16/16 PASS)
└── tasks.md  (/speckit-tasks で生成)
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── history.py        # recent_form / aptitude / class_transition を as-of で追加（_cumulative_before 同型）
├── human_form.py     # NEW: jockey/trainer as-of win_rate（daily-minus-current＝対象行+同日除外）
├── registry.py       # 新特徴を FeatureMeta + group で登録、FEATURE_GROUPS マップ
└── builder.py        # 新特徴を matrix に結線

training/src/horseracing_training/
└── ...               # 正則化レンジ固定 + fold 内 inner split 選択フック

eval/src/horseracing_eval/
├── feature_eval.py   # NEW: fold 内選択 walk-forward + AdoptionReport（LogLoss/ECE/fold 別差）
├── ablation.py       # NEW: group ablation
├── market_edge.py    # NEW: p−q calibration / edge bucket / q 条件付き LogLoss（diagnostic）
└── cli.py            # feature-eval / feature-ablation / feature-diagnostic
```

**Structure Decision**: 既存 features/training/eval を拡張（新パッケージなし）。新特徴は registry の実証済み
リーク機構を転用。スキーマ変更なし。

## Complexity Tracking

> Constitution 違反なし（スキーマ変更なし、既存リーク機構転用、評価先行を強化）。記入不要。
