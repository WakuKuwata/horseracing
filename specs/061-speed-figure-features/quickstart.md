# Quickstart: 061 本格スピード指数特徴

## 前提

- ローカル Postgres(horseracing、port 15432)+ 2007+ ingest 済み
- `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`

## 1. ユニット+パリティ

```bash
cd features && uv run pytest tests/ -k speed_figure -x && uv run pytest tests/ -q
```

期待: 新テスト緑(INV-F1 挙動 leak-guard・INV-F2 additive・INV-F4 NaN 規律)+ 既存全緑。

## 2. Spike(go/no-go)

```bash
cd training && uv run python -m horseracing_training feature-eval \
  --drop-groups speed_figure --from 2021-01-01   # 直近窓で binary A/B
```

期待: cand(新群あり)win LogLoss < base。微小(<0.0005)なら pl_topk model-eval でも確認。

## 3. フル事前登録ゲート

```bash
uv run python -m horseracing_training feature-eval --drop-groups speed_figure
```

期待: contracts の 3 条件の機械判定 + カバレッジレポート。

## 4. 通過時: production 再学習

```bash
uv run python -m horseracing_training train-evaluate \
  --objective pl_topk --calibration isotonic --target-encode jockey_id,trainer_id \
  --baseline lgbm-057 --model-version lgbm-061 --first-valid-year 2008
```

期待: lgbm-057 比で全指標非悪化 → active 昇格はユーザー判断。

## 5. serving 互換 E2E(INV-F5)

```bash
# features-016 registry 下で:
# lgbm-057 予測 == persisted 値(バイト一致)/ lgbm-058-acc・lgbm-060-mkt compat-load 成功
cd serving && uv run pytest tests/ -q
# + 実 DB スクリプトで mismatch 0 を確認(058 T013 同型)
```

## 6. 再 materialize(FEATURE_VERSION 変更のため)

```bash
cd features && uv run python -m horseracing_features materialize
```
