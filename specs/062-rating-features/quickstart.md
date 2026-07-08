# Quickstart: 062 as-of レーティング特徴

## 前提

- ローカル Postgres(horseracing、port 15432)+ 2007+ ingest 済み
- `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`

## 1. ユニット(レーティング正しさ + materialize 安全)

```bash
cd features && uv run pytest tests/ -k rating -x && uv run pytest tests/ -q
```

期待: INV-R1 挙動 leak-guard・INV-R2 pool-end 非依存・INV-R3 決定論・INV-R6 レーティング正しさ緑 + 既存全緑。

## 2. パリティ + 再 materialize + カバレッジ

```bash
# in-memory vs materialized bit 一致（実 DB スクリプト、061 と同型）+ 共有列 additive 一致
cd features && uv run python -m horseracing_features materialize
```

期待: 共有列 check_exact+check_dtype 一致・rating 列カバレッジ報告(初出走以外は水準あり)。

## 3. Spike(go/no-go)

```bash
cd training && uv run python -m horseracing_training feature-eval --drop-groups rating --from 2021-01-01
# 微小・重複懸念のため pl_topk group-marginal も直近 fold で確認（061 と同じ inline スクリプト）
```

期待: binary で cand < base、pl_topk でも CAND 勝ち。Elo が既存能力と重複して pl_topk で消えないか確認。

## 4. フル事前登録ゲート

```bash
uv run python -m horseracing_training feature-eval --drop-groups rating
```

期待: contracts の 3 条件の機械判定 + カバレッジ。

## 5. 通過時: production 再学習 lgbm-062

```bash
uv run python -m horseracing_training train-evaluate \
  --objective pl_topk --calibration isotonic --target-encode jockey_id,trainer_id \
  --baseline lgbm-061 --model-version lgbm-062 --first-valid-year 2008
```

期待: lgbm-061 比で全指標非悪化 → active 昇格はユーザー判断。**train-evaluate は旧 active を retire しないので手動 retire 必須**(061 教訓)。

## 6. serving 互換 E2E(INV-R7)

```bash
cd serving && uv run pytest tests/ -q
# + 実 DB: lgbm-061 予測 == persisted 値・058-acc/060-mkt compat-load（061 の compat_e2e スクリプト同型）
```
