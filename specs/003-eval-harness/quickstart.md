# Quickstart: 評価ハーネス検証

実装後に評価が end-to-end で動くことを確認する手順。実装詳細は tasks.md / 実装フェーズ。

## 前提

- Feature 001 適用済み + Feature 002 でデータ取込済みの PostgreSQL (`DATABASE_URL`)。
- Docker (テストの testcontainers 用)。
- `eval/` パッケージの依存をインストール (`uv sync`、`horseracing-db` にパス依存)。

## セットアップ

```bash
cd eval
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 取込済み DB
```

## baseline 評価 (ローカルスモーク)

```bash
# 市場 baseline と一様 baseline を walk-forward 評価し model_versions に保存
uv run python -m horseracing_eval evaluate-baseline --baseline market
uv run python -m horseracing_eval evaluate-baseline --baseline uniform
```

期待: label 別 (win/top2/top3) の LogLoss/Brier/AUC/NDCG/ECE と fold 別サマリが表示され、
`model_versions` に `model_family='baseline'` の行が追加され `metrics_summary` に格納される。
市場 baseline の LogLoss が一様 baseline を下回る (SC-004)。

## テスト

```bash
cd eval
uv run pytest                 # 全テスト
uv run pytest tests/unit      # 指標数値・整合性 fail-fast・Harville・splits (DB 不要、合成データ)
uv run pytest -m integration  # 実 DB で baseline walk-forward 評価 (testcontainers)
```

検証する受け入れ基準:

- **SC-001**: 合成データ (既知の確率・着順) で LogLoss/Brier/AUC/NDCG/ECE が手計算の期待値と一致。
- **SC-002**: 範囲外確率・レース内合計逸脱が `ConsistencyError` で fail-fast される。
- **SC-003 / SC-004**: 取込データで市場・一様 baseline を walk-forward 評価でき、市場が一様を LogLoss で
  上回る。
- **SC-005**: baseline 結果が `model_versions.metrics_summary` に保存され再読込できる。
- **SC-006**: 同一入力・同一分割で 2 回評価して完全一致 (決定論)。

## 整合性・母集団の確認 (参考)

```python
# 取消・除外が母集団から除外され、derive_labels (finished のみ) と一致することを確認
```
