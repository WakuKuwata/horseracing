# Implementation Plan: 評価ハーネスと baseline

**Branch**: `003-eval-harness` | **Date**: 2026-06-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-eval-harness/spec.md`

## Summary

学習より先に評価基盤を用意する。新パッケージ `eval/` (`horseracing-eval`、`horseracing-db` に依存) に、
expanding-window walk-forward 分割、Predictor Protocol、予測品質指標 (LogLoss/Brier/AUC/NDCG/ECE)、
確率整合性の fail-fast 検証、2 つの baseline (市場=人気順 / 一様) を実装する。baseline 評価結果は既存
`model_versions.metrics_summary` に保存 (スキーマ変更なし)。最大リスク (結果確定 odds/popularity の特徴量
混入) は責務境界の明文化で防ぐ。

## Technical Context

**Language/Version**: Python 3.12 (既存パッケージと同一)

**Primary Dependencies**: `horseracing-db` (パス依存)、numpy、scikit-learn (LogLoss/Brier/AUC/NDCG の
標準実装)、SQLAlchemy 2.0 (DB 読取)。ECE と確率整合性・Harville は自前実装。

**Storage**: PostgreSQL 16。読取 = races/race_results/race_horses/`labels.derive_labels`。書込 =
`model_versions` (baseline を model_family='baseline' で 1 行、`metrics_summary` jsonb)。

**Testing**: pytest。合成データで指標の数値正しさ・整合性 fail-fast をユニット検証。testcontainers で
実 DB を使い baseline の walk-forward 評価を統合検証。

**Target Platform**: Linux / macOS のオペレーター実行 (手動 CLI / API)

**Project Type**: 単一の評価パッケージ (`horseracing-eval`)

**Performance Goals**: ~3,400 races/年 × valid 年数。Harville top3 は O(N^3)/race だが N<=18 で十分高速。

**Constraints**: 決定論的 (乱数なし、ランダム CV 禁止)、2007+ 境界 (`is_in_ingest_scope`)、確率整合性は
label 別の設定可能な絶対誤差 (既定 0.05/0.10/0.15) で fail-fast。

**Scale/Scope**: 評価対象 ~数十万 race-horse 行。baseline は状態なし (fit は no-op)。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: 既存データを読み、2007 境界は `is_in_ingest_scope`。ラベルは `labels.derive_labels`
  (1着率/2着以内率/3着以内率) を正本に使う。新 ID は作らない。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: walk-forward は expanding train で valid が常に train より後。
  市場 baseline は結果確定 odds を使うが「参照線専用・モデル特徴量に使わない」を FR-013 で境界化。本
  feature は特徴量を作らない。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 本 feature が評価ハーネスそのもの。学習より先に walk-forward 評価・
  baseline 比較・ECE を提供する。**PASS (本原則を充足する feature)**
- [x] **IV. 確率整合性**: ハーネスが `0<=win<=top2<=top3<=1` とレース内合計 (許容誤差付き) を検証し違反は
  fail-fast。取消・除外を母集団から除外し再正規化。**PASS**
- [x] **V. 再現性・監査**: 評価は決定論的。baseline 結果を `model_versions.metrics_summary` に評価条件
  (窓スキーム・許容誤差・指標) と共に保存。**PASS**
- [x] **VI. feature 分割規律**: MVP はスキーマ変更なし (metrics_summary 利用)。永続化テーブル (US4) は P2 で
  非破壊拡張。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion を spec 段階で取得・記録 (下記)。本 plan はその
  設計を実行するもので新たな非自明分岐なし。**PASS**

### Second Opinion 記録 (codex:codex-rescue — spec 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| MVP スコープ | P1=品質指標+walk-forward+baseline+ECE、ROI は P2 (憲法 III は ROI 必須化していない) | 採用 (US1/US2 P1、US3 ROI P2) |
| baseline 設計 | 人気順 (1/odds 正規化 + top2/top3 は Harville) + 一様 1/N の二本立て、人気順はリーク明示 | 採用 (FR-009〜011) |
| walk-forward | race_date 固定分割を今 spec 化、ランダム CV 禁止、fold ごと train-only 集計を契約化 | 採用 (expanding train + 年次 valid、research R1) |
| Predictor 抽象 | レース単位で全頭 win/top2/top3 を返す最小 Protocol。整合性はハーネスが fail-fast | 採用 (contracts/predictor.md) |
| 保存先 | MVP は model_versions.metrics_summary + レポート。eval_runs 正規化は P2 | 採用 (FR-012、US4 P2) |
| ECE 注意 | label 別 binning、非完走・非出走除外、頭数別診断、同着は derive_labels に従う | 採用 (research R4) |
| 最大リスク | 結果確定 odds/popularity の将来特徴量混入。境界を spec 明記、許容誤差・窓・bin を明示 | 採用 (FR-013、許容誤差 0.05/0.10/0.15、窓スキーム確定) |

不採用・保留: なし。

## Project Structure

### Documentation (this feature)

```text
specs/003-eval-harness/
├── plan.md
├── research.md          # 窓スキーム・指標定義・Harville・ECE・整合性許容誤差
├── data-model.md        # 評価データセット・母集団・ラベル・metrics_summary スキーマ
├── quickstart.md        # baseline 評価の実行 + テスト手順
├── contracts/
│   ├── predictor.md     # Predictor Protocol と整合性契約
│   └── metrics.md       # 指標の入出力契約 (label 別、母集団、ECE)
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
eval/                                    # 新パッケージ horseracing-eval
├── pyproject.toml                       # horseracing-db (path) + numpy + scikit-learn + sqlalchemy
├── src/horseracing_eval/
│   ├── __init__.py
│   ├── predictor.py                     # Predictor Protocol (fit no-op 可 / predict_race)
│   ├── splits.py                        # expanding-window walk-forward (race_date 基準)
│   ├── dataset.py                       # DB から評価データセット構築 (母集団・ラベル・odds)
│   ├── consistency.py                   # 確率整合性検証 (range + race-sum tolerance, fail-fast)
│   ├── metrics.py                       # LogLoss/Brier/AUC/NDCG (sklearn) + ECE (自前, label別/頭数別)
│   ├── harness.py                       # walk-forward 評価オーケストレーション (決定論的)
│   ├── baselines.py                     # MarketBaseline (1/odds+Harville) / UniformBaseline
│   ├── store.py                         # baseline 結果を model_versions.metrics_summary に保存
│   └── cli.py                           # argparse: evaluate-baseline
└── tests/
    ├── unit/                            # metrics 数値正しさ・consistency fail-fast・Harville・splits
    └── integration/                     # 実 DB で baseline walk-forward 評価 (testcontainers)
```

**Structure Decision**: 評価は application ロジックなので `db/` とは分離し、新パッケージ `eval/`
(`horseracing-eval`、`horseracing-db` にパス依存) を作る。`ingest/` と同じ層構成。MVP はスキーマ変更なし。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
