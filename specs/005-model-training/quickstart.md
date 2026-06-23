# Quickstart: モデルトレーニングと校正の検証

実装後に「学習→評価→採用」が end-to-end で動き、校正が fold 安全であることを確認する手順。

## 前提

- Feature 001 適用 + 002 取込済み + 003 で baseline 保存済み (market/uniform) の PostgreSQL。
- Docker (testcontainers 用)。
- `training/` の依存をインストール (`uv sync`、db/features/eval にパス依存、lightgbm/scikit-learn)。

## セットアップ

```bash
cd training
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 取込済み + baseline 保存済み DB
```

## 学習・評価・採用 (ローカルスモーク)

```bash
uv run python -m horseracing_training train-evaluate \
    --first-valid-year 2008 --calibration platt --ece-threshold 0.05
```

期待: walk-forward で fold ごとに LightGBM 学習 + 校正 → harness 評価 → baseline と比較 → 採用判定。
label 別 (win/top2/top3) 指標と採用結果 (active/candidate) を表示し、`model_versions` に行 + artifacts を保存。
win LogLoss が uniform baseline を下回る。

## テスト

Docker 必須 (testcontainers が PostgreSQL を起動し `db/` migration を head まで適用)。

```bash
cd training
uv run pytest                 # 全テスト
uv run pytest tests/unit      # 整合性・校正 fold 漏れ・採用ゲート・決定論 (合成データ)
uv run pytest -m integration  # 実 DB で学習→評価→保存スモーク
```

検証する受け入れ基準:

- **SC-001 (整合性)**: Predictor の出力が全 valid レースで `0<=win<=top2<=top3<=1`・Σ 許容内 (harness
  fail-fast を通る)。
- **SC-002 (校正 fold 漏れ)**: 校正器が valid 期間の race を一切参照しない (合成データで「valid に好成績を
  仕込んでも校正器が変わらない」ことを assert)。校正で win ECE が改善。
- **SC-003 (baseline 超え)**: LightGBM が walk-forward 評価で uniform baseline を win LogLoss で上回る。
- **SC-004 (採用ゲート)**: ゲート合格モデルが active、不合格が candidate。
- **SC-005 (保存)**: `model_versions` に metrics_summary + weights_uri + calibrator_uri、metadata に
  seed/params/fold/feature hash。
- **SC-006 (決定論)**: 同一データ・同一 fold・同一 seed で 2 回実行して指標が完全一致。

## 校正 fold 漏れ検査の考え方 (SC-002 の具体)

合成データで、valid 期間のレースに極端な結果を仕込んでも校正器のパラメータ (および校正後の valid 予測の
校正写像) が変化しないことを assert する。変化すれば valid が校正に漏れている (035/036 の再発)。
