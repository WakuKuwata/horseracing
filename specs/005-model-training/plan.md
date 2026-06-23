# Implementation Plan: モデルトレーニングと校正

**Branch**: `005-model-training` | **Date**: 2026-06-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/005-model-training/spec.md`

## Summary

新パッケージ `training/` (`horseracing-training`、db/features/eval 依存) に、単一 win LightGBM を
Feature 003 の Predictor として実装する。fold ごとに leak-safe 特徴量 (Feature 004、started 全頭・
DNF win=0) で学習し、校正器を train 内 held-out で fit (valid/test 不使用)、推論で raw→校正→clip→
レース内正規化→Harville で top2/top3 を導出して確率整合性を機構保証する。評価ハーネスで baseline と
同一条件比較し、採用ゲート (全 label + ECE) で `model_versions` に candidate/active を保存。MVP は
スキーマ変更なし・target encoding 不使用・固定ハイパラ。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `horseracing-db` / `horseracing-features` / `horseracing-eval` (パス依存)、
LightGBM、scikit-learn (校正・指標)、numpy、pandas、SQLAlchemy 2.0

**Storage**: PostgreSQL 16 (読: 特徴量/ラベル、書: `model_versions`)。モデル成果物はファイルシステム
(`artifacts/model_versions/{model_version}/` に `model.txt` / `calibrator.pkl` / `metadata.json`)。

**Testing**: pytest。合成データで Predictor 整合性・**校正 fold 漏れ検査**・採用ゲート・決定論を検証。
testcontainers で実 DB を使い学習→評価のスモーク。

**Target Platform**: Linux / macOS の学習パイプライン実行 (手動 CLI)

**Project Type**: 単一の学習パッケージ (`horseracing-training`)

**Performance Goals**: fold-train ~数万〜数十万 race-horse 行。LightGBM 学習は数秒〜分。特徴量は全レース
一度計算してキャッシュ (as-of で leak-safe)。

**Constraints**: 単一 win + 正規化 + Harville + clip で整合性。校正は train-only。決定論 (seed 固定)。
結果確定 odds/popularity・ResultMarket をモデルが参照しない。target encoding は MVP 不使用。

**Scale/Scope**: fold 数 = valid 年数。各 fold で 1 モデル + 1 校正器。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: 既存データを読み、ラベルは `labels.derive_labels`、2007 境界整合。新 ID なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 特徴は Feature 004 の as-of (race_date<R)。**校正器を train-only
  (held-out) で fit し valid/test を見ない** (035/036 回避)。target encoding は MVP 不使用 (OOF 漏れ回避)。
  ResultMarket/結果確定オッズをモデルが参照しないリーク検査。fold 境界片側適用漏れを検査。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: モデルは Feature 003 の harness で walk-forward 評価。採用は
  baseline 比較 + ECE のゲート。**PASS (本原則を充足)**
- [x] **IV. 確率整合性**: 単一 win → clip → レース内正規化 → Harville で `0<=win<=top2<=top3<=1`・Σ≈1/2/3 を
  機構保証。harness の fail-fast を通す。**PASS**
- [x] **V. 再現性・監査**: seed 固定で決定論。`model_versions` に metrics_summary、artifacts に
  weights/calibrator/metadata (seed/params/fold 境界/校正方式/feature hash/git sha)。**PASS**
- [x] **VI. feature 分割規律**: MVP スキーマ変更なし (model_versions 既存列 + ファイル artifacts)。ハイパラ
  探索・OOF encoding は P2。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion を spec 段階で取得・記録 (下記)。本 plan は
  その設計を実行。**PASS**

### Second Opinion 記録 (codex:codex-rescue — spec 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| モデル設計 | 単一 win + 校正 + Σ=1 正規化 + Harville が MVP に妥当。3 ラベル別は校正・fold が 3 倍で 035/036 型漏れ増 | 採用 (R1) |
| 校正 fold 安全 | train 内 held-out/OOF のみ、valid/test 不使用。**既定 Platt** (isotonic は端点で Harville 破壊)。順序 raw→校正→clip→正規化→Harville | 採用 (R2)。MVP は時系列 held-out |
| 学習母集団 | started 全頭・DNF→0 (finished-only は非完走リスク馬を過大評価)。harness は finished 採点の母集団ミスマッチを記録 | 採用 (R3)。ミスマッチを既知バイアスとして記録 |
| walk-forward 連携 | fold ごと再学習、特徴 as-of。fold 境界片側適用漏れ・TE の fit-all-train 漏れが最大リスク | 採用 (R4)。TE は MVP 不使用 |
| 確率整合性 | 非退化 win なら Harville Σ は許容内。端点で Σtop3 割れ → clip/floor 必須 | 採用 (R5、clip) |
| 採用判定 | win LogLoss 単体不足、全 label + ECE を gate に。閾値は事前固定 | 採用 (R6) |
| 保存 | スキーマ変更なし。weights_uri/calibrator_uri + metadata.json (git sha/feature hash/params/seed/fold/校正方式) | 採用 (R7) |
| 横断リスク | ResultMarket 参照禁止テスト、weight/diff は POST_WEIGHT、少データ年/同着/小頭数/年跨ぎ/valid ハイパラ選択 | 採用 (リーク検査・タイミング allowlist) |

最重要リスク TOP3: ①校正の fold 漏れ ②確率整合性 (端点 Harville) ③特徴/付帯情報の隠れリーク。
①は train-only held-out + 漏れ検査、②は clip、③は model_input_features + ResultMarket 非参照検査で対応。

## Project Structure

### Documentation (this feature)

```text
specs/005-model-training/
├── plan.md
├── research.md          # モデル設計・校正 fold 安全・母集団・Harville 再利用・採用ゲート・成果物
├── data-model.md        # TrainedPredictor・校正・採用ゲート・metrics_summary/artifacts スキーマ
├── quickstart.md        # 学習→評価→採用 + 校正 fold 漏れ検査手順
├── contracts/
│   ├── predictor.md     # LightGBMPredictor の Predictor 契約 + 推論順序の不変条件
│   └── adoption.md      # 採用ゲート・保存の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
training/                                  # 新パッケージ horseracing-training
├── pyproject.toml                         # db/features/eval (path) + lightgbm + scikit-learn + numpy + pandas
├── src/horseracing_training/
│   ├── __init__.py
│   ├── dataset.py                         # 特徴量(Feature 004) + win ラベル(started 全頭, DNF=0) を結合
│   ├── calibration.py                     # Platt/isotonic, train 内 held-out で fit (valid 不使用)
│   ├── win_model.py                       # LightGBM win 学習 (seed 固定)
│   ├── predictor.py                       # LightGBMPredictor: fit/predict_race (raw→校正→clip→正規化→Harville)
│   ├── adoption.py                        # 採用ゲート (全 label + ECE 閾値)
│   ├── artifacts.py                       # weights/calibrator/metadata 保存 + model_versions 登録
│   └── cli.py                             # train-evaluate --first-valid-year ... --calibration platt
└── tests/
    ├── unit/                              # 整合性・校正 fold 漏れ・採用ゲート・決定論・Harville
    └── integration/                       # 実 DB で学習→評価→保存スモーク (testcontainers)
```

**Structure Decision**: 学習は application ロジックなので新パッケージ `training/` を作り、db/features/eval に
パス依存。Predictor は eval の harness が消費。Harville は eval の関数を再利用 (公開関数として参照)。MVP
スキーマ変更なし。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
