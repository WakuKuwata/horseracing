# Implementation Plan: 予測 serving(推論専用パイプライン)

**Branch**: `006-prediction-serving` | **Date**: 2026-06-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/006-prediction-serving/spec.md`

## Summary

新パッケージ `serving/`(`horseracing-serving`、db/features/eval/training 依存)に、採用済み(active)
モデルを成果物からロードして推論する専用パイプラインを実装する。Feature 004 の as-of leak-safe 特徴量を
対象レース(結果未確定の未来レース含む)に対して構築し、Feature 005 と同一順序(raw→校正→clip→レース内
正規化→Harville)で校正済み win/top2/top3 を算出、`prediction_runs`/`race_predictions`/`feature_snapshots`
に永続化する。**学習はしない**(採用済みモデルの推論専用)。スキーマ変更なし。

codex second opinion で判明した BLOCKER を本 plan で解消する:**学習成果物が前処理器(target encoder・
特徴量列順・categorical 方針)を保存していない**ため、US4(target encoding)で学習したモデルは現状 serving
不能。これを Feature 005 の成果物保存の非破壊拡張(`preprocessor.pkl` 追加)+ serving 側の検証付きロード経路で
解消する。既存の TE 不使用モデル(`lightgbm-win-v1`)は feature_hash 一致を条件に列順を再構成して serving 可、
TE 使用かつ前処理器欠落のモデルは fail-fast。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `horseracing-db` / `horseracing-features` / `horseracing-eval` /
`horseracing-training`(パス依存)、LightGBM(booster ロード)、scikit-learn(校正器)、numpy、pandas、
SQLAlchemy 2.0

**Storage**: PostgreSQL 16(読: 特徴量 / model_versions、書: prediction_runs / race_predictions /
feature_snapshots)。モデル成果物はファイルシステム(`artifacts/model_versions/{model_version}/`)。

**Testing**: pytest + testcontainers。合成データで推論整合性・**リーク検査**・**決定論**・
**前処理器ロード往復**・スキーマ不一致 fail-fast・結果未確定 as-of を検証。実 DB スモーク。

**Target Platform**: Linux / macOS の手動 CLI 実行(serving)

**Project Type**: 単一の serving パッケージ(`horseracing-serving`)

**Performance Goals**: 1 レース〜1 日分(数十レース)の推論を秒オーダー。特徴量は対象 end_date で一度構築。

**Constraints**: 結果由来情報(ResultMarket / race_results)をモデルが参照しない。as-of で未来リークなし。
決定論(成果物固定で同一出力)。学習時 feature_hash / feature_version と推論時スキーマ一致を fail-fast 検証。

**Scale/Scope**: active モデル 1 つを serving。レース母集団 = `entry_status='started'`。スキーマ変更なし。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: 既存 `race_id`(12桁)・2007+ 取込データ・`model_versions` を読む。新 ID 体系なし。
  ラベルは win/top2/top3(`1着率`/`2着以内率`/`3着以内率`)。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 特徴は Feature 004 の as-of(`race_date<R`、同日除外)。結果確定
  オッズ/人気(ResultMarket)・着順(race_results)をモデル入力に使わない(リーク検査)。未来レースでも当該
  レース当日以降を混ぜない。**学習時 feature_hash と推論時スキーマ不一致は fail-fast**。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: serving は学習・特徴変更を行わず、Feature 003/005 で walk-forward
  評価・baseline 比較・ECE を通過し**採用済み(active)のモデルのみ**を推論に使う。評価ハーネスは既存。**PASS**
- [x] **IV. 確率整合性**: raw→校正→clip→レース内正規化→Harville で `0<=win<=top2<=top3<=1`・Σ 許容内を
  機構保証。`check_consistency`(Feature 003)+ `PROB_MONOTONIC`(DB 制約)で fail-fast。取消・除外は母集団
  から除外。**PASS**
- [x] **V. 再現性・監査**: `prediction_runs`(model_version/logic_version/computed_at)+ `feature_snapshots`
  (feature_version + 前処理後 model-input ベクトル)を保存。本フィーチャーは本原則の実装そのもの。**PASS**
- [x] **VI. feature 分割規律**: UI なし。API/DB 契約(prediction_runs/race_predictions/feature_snapshots)は
  Feature 001 で確定済み。スキーマ変更なし。成果物形式の拡張(前処理器)は非破壊。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion を取得・記録(下表)。BLOCKER を本 plan で解消。**PASS**

### Second Opinion 記録(codex:codex-rescue — spec/plan 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| 未来レース as-of 安全性 | OK。history は `race_date<R` strict / `allow_exact_matches=False` / cumsum−当日。target に race_results 不要 | 採用(R1)。build_feature_matrix を end_date=対象日で使用 |
| **モデルロード経路** | **BLOCKER**: serving 用ロード経路が無い。`predict_race` は session 依存・matrix 再構築 | serving に検証付き `load_serving_model`→`predict` を新設(R2) |
| **前処理器の保存欠落** | **BLOCKER**: TargetEncoder/列順/categorical 語彙が calibrator.pkl にも metadata にも無い。TE モデルは復元不能 | training 成果物を非破壊拡張し `preprocessor.pkl` を保存。legacy 無 TE は再構成+feature_hash 検証、TE 欠落は fail-fast(R2/R3) |
| 母集団 | RISK: `started` が確定出走を意味するかは ingest タイミング依存 | `entry_status='started'` を確定出走とみなす前提を明記、混入防止は ingest 責務(R4) |
| 決定論・冪等性 | RISK: 再実行は append-only 新 run。`logic_version` 未定義 | logic_version を feature_version+serving ロジック版で定義。再実行は新 run 追記(R5) |
| feature_snapshots 内容 | RISK: raw だけでは TE 再現不可 | 前処理後 model-input ベクトル(+raw/calibrated 補助)を保存(R6) |
| やってはいけない | model.txt+calibrator のみロード / 母集団を結果から取る / 破壊的 upsert | 3 点とも回避(R2/R4/R5) |

最重要リスク TOP3: ①前処理器欠落で TE モデル serving 不能(BLOCKER)②保存済みモデルの特徴スキーマ整合検証
③未来レースでの母集団・as-of。①は成果物拡張+検証ロード、②は feature_hash/feature_version fail-fast、
③は entry_status ベース母集団 + as-of 再利用で対応。

## Project Structure

### Documentation (this feature)

```text
specs/006-prediction-serving/
├── plan.md
├── research.md          # ロード経路・前処理器保存・as-of・母集団・logic_version・snapshot 内容
├── data-model.md        # ServingModel・推論不変条件・3 テーブル書き込み・前処理器成果物スキーマ
├── quickstart.md        # 推論 → 永続化 → 監査・決定論・リーク検査手順
├── contracts/
│   ├── serving.md       # load_serving_model / predict_race / run_serving の契約
│   └── artifacts.md     # 前処理器成果物(preprocessor.pkl)と後方互換ロードの契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
serving/                                   # 新パッケージ horseracing-serving
├── pyproject.toml                         # db/features/eval/training (path) + lightgbm + sklearn
├── src/horseracing_serving/
│   ├── __init__.py
│   ├── model_loader.py                    # active 解決 + ServingModel ロード(検証付き、後方互換)
│   ├── predictor.py                       # ServingPredictor: 特徴(as-of) → 前処理 → booster → 校正 → clip → 正規化 → Harville
│   ├── persistence.py                     # prediction_runs / race_predictions / feature_snapshots 書き込み
│   ├── pipeline.py                        # run_serving(race_id or date): 推論 → 整合性検査 → 永続化
│   └── cli.py                             # predict --race-id / --date / --model-version
└── tests/
    ├── unit/                              # 整合性・前処理器往復・スキーマ不一致 fail-fast・logic_version
    └── integration/                       # 実 DB で推論→保存→監査→決定論→リーク→未来レース as-of

training/  (非破壊拡張)
└── src/horseracing_training/artifacts.py  # preprocessor.pkl(encoders/列順/categorical/te_smoothing)を追加保存
```

**Structure Decision**: serving は学習と責務が異なる(採用済みモデルの推論・永続化)ため新パッケージ
`serving/` を作り、training の純粋部品(WinModel.predict 相当の booster 呼び出し・Calibrator.transform・
TargetEncoder.transform・assemble_predictions)と features の `build_feature_matrix`、eval の
`check_consistency` を再利用する。前処理器の保存は training 側成果物の非破壊拡張で満たす。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
