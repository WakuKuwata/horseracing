# Quickstart: 060 市場残差型・精度最優先モデル

## 前提

- ローカル Postgres(horseracing DB)起動済み・2007+ データ ingest 済み
- 全パッケージ `uv sync` 済み

## 1. Spike(go/no-go、フル実装前)

```bash
# 合成データ検証はユニットテストで実行
uv run --project training pytest training/tests -k market_offset -x

# 実 DB 少数 fold(直近 3-4 fold)比較
uv run --project training python -m horseracing_training model-eval \
  --objective pl_topk --market-offset --folds-tail 4
# 出力: candidate vs market-q baseline vs 058-acc 構成(同一制限母集団)の win/top2/top3 LogLoss
```

期待: candidate の win LogLoss < q baseline(平均)。負ければ中断(contracts/market-offset.md)。

## 2. フル評価(事前登録ゲート)

```bash
uv run --project training python -m horseracing_training train-evaluate \
  --objective pl_topk --calibration isotonic \
  --target-encode jockey_name --target-encode trainer_name \
  --market-offset
```

期待: 19-fold で 3 ゲート(vs q baseline / vs 058-acc 再評価 / top2,top3 非悪化)の機械判定が出る。
オッズ欠損による除外レース件数・期間分布がレポートに含まれる。

## 3. 登録(ゲート全通過時のみ)

```bash
# 非 active candidate 登録(自動昇格なし)
uv run --project training python -m horseracing_training register-model --model-version lgbm-060-mkt
uv run --project training python -m horseracing_training set-model-label \
  --model-version lgbm-060-mkt \
  --display-name "市場残差・精度最優先" \
  --purpose "市場情報(今走オッズ)利用の精度最優先モデル。意思決定支援には非使用。closing-leaning オッズによる retrospective 評価が主用途"
```

## 4. Serving E2E 検証

```bash
# default モデル byte-parity(モデル指定なし → lgbm-057/058 系のまま・予測値不変)
uv run --project serving python -m horseracing_serving predict --race-id <RACE_ID>

# market_offset モデル明示指定(オッズありレース → 予測永続化、lv に mkt=logq)
uv run --project serving python -m horseracing_serving predict --race-id <RACE_ID> --model-version lgbm-060-mkt

# オッズなしレース → typed skip(予測行を作らない)ことを確認
```

## 5. 全スイート

```bash
for p in training serving eval features; do (cd $p && uv run pytest -q); done
uv run ruff check .
```

期待: 既存テスト全緑(default 経路回帰ゼロ)+ 新規 market_offset テスト緑。
