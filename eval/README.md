# horseracing-eval

学習より先に用意する評価ハーネス。walk-forward 分割、Predictor 抽象、予測品質指標
(LogLoss/Brier/AUC/NDCG/ECE)、確率整合性の fail-fast 検証、baseline(市場=人気順 / 一様)を提供する。
`horseracing-db` に依存。

- 仕様: [specs/003-eval-harness](../specs/003-eval-harness/)
- スタック: Python 3.12, numpy, scikit-learn, SQLAlchemy 2.0

## 責務境界 / provenance (FR-013, 憲法 II)

結果確定時の `odds` / `popularity` の扱いを明確に分離する:

- **baseline(評価)**: 市場 baseline は結果確定 odds を**参照線として**使う(`MarketBaseline.is_leaky_reference = True`)。
  これは「市場という超えるべきバー」を測るためで、過去評価専用。
- **特徴量フィーチャー(将来)**: 結果確定 odds/popularity を**モデル特徴量に使ってはならない**(リーク)。
  Predictor に渡る `HorseEntry.result_market` は市場 baseline 専用フィールドであり、feature-based
  predictor は参照しない。
- **serving(将来)**: 発走前に利用可能な情報のみ。

## セットアップ

```bash
cd eval
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 001 適用 + 002 で取込済みの DB
```

## baseline 評価

```bash
uv run python -m horseracing_eval evaluate-baseline --baseline market
uv run python -m horseracing_eval evaluate-baseline --baseline uniform
```

label 別(win/top2/top3)の指標と fold 別サマリを表示し、`model_versions`
(`model_family='baseline'`)の `metrics_summary` に保存する。市場 baseline の win LogLoss が一様
baseline を下回る(SC-004)。

## テスト

Docker 必須(testcontainers が PostgreSQL 16 を起動し `db/` の migration を head まで適用)。

```bash
uv run pytest                 # 全テスト
uv run pytest tests/unit      # 指標・整合性・splits・baseline(DB 不要、合成データ)
uv run pytest -m integration  # 実 DB で baseline walk-forward 評価・保存・比較
```

## 設計の要点

- walk-forward = **expanding-window train + 年次 valid**(2007 = 初期 train 専用、評価は 2008 から)。
- 確率整合性: 各馬 `0<=win<=top2<=top3<=1`、レース内合計は label 別の設定可能な絶対誤差(既定
  0.05/0.10/0.15)。違反は `ConsistencyError` で fail-fast。
- 母集団 = started 馬(取消・除外を除外)、採点は finished のみ(`labels.derive_labels` を正本)。
- 市場 baseline: `1/odds` 正規化 + Harville で top2/top3。一様 baseline: 1/N(cap)。
- 評価結果は `model_versions.metrics_summary`(jsonb)に保存。fold 別比較は `report.compare`。
  正規化テーブル(eval_runs)は必要になるまで deferred(FR-015「必要なら」)。
