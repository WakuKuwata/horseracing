# Quickstart: 予測根拠表示 (040) — 検証ガイド

前提: horseracing DB（`postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`）、active モデル lgbm-039、migration head 0008。

## 0. T0 de-risk spike（実装前・最重要）

```bash
cd training
DATABASE_URL=... uv run python -m <spike script>   # tasks の T0 が用意
```

期待: serving と同一の X（TE 適用後・feature_cols 順）で
`contrib[:, :-1].sum(axis=1) + contrib[:, -1] ≈ booster.predict(X, raw_score=True)`（rel 1e-6）
+ pred_contrib レイテンシ 1 レース +100ms 未満。**不成立なら中断**。

## 1. migration とユニット

```bash
cd db && DATABASE_URL=... uv run alembic upgrade head   # 0008
cd training && uv run pytest tests/unit/test_explanation.py -q
cd serving && uv run pytest -q          # p バイト一致 + persist explanation
cd api && uv run pytest -q              # importance/divergence + read-only 維持
cd features && uv run pytest -q         # leak-guard 拡張
```

## 2. 実データ end-to-end

```bash
# 予測を 1 レース生成（explanation 付き）
cd serving && DATABASE_URL=... uv run python -m horseracing_serving <race_id>
# 確認: explanation JSONB が保存され加法性が成立
psql: SELECT explanation->>'score', explanation->'items' FROM race_predictions
      WHERE prediction_run_id = '<新 run>' LIMIT 3;
```

期待: `explanation` 非 NULL、`base_value+Σitems+other == score`、win_prob は explanation なし時代の同条件予測とバイト一致。

## 3. API

```bash
cd api && DATABASE_URL=... uv run uvicorn horseracing_api.app:app &
curl :8000/api/v1/races/<race_id>/predictions | jq '.horses[0] | {explanation, divergence}'
curl :8000/api/v1/models/lgbm-039/importance | jq '.values[:3]'
curl :8000/api/v1/models/lgbm-036/importance   # 旧モデル → 404 importance_unavailable
```

## 4. front

```bash
cd front && pnpm test          # ExplanationPanel/ImportanceChart/DivergenceBadge + 注記不変条件
pnpm run typegen && git diff --exit-code openapi.json src/api/types.ts  # drift-check
pnpm dev                       # RaceDetailPage: 馬行展開でスコア寄与・限界注記・バッジ確認
```

目視チェックリスト: 限界注記 2 種が常時表示 / te_* に「導出特徴」バッジ / バッジ文言が純事実比較のみ / 乖離ソートが存在しない / explanation NULL の旧 run で「未提供」。

## 5. 回帰

```bash
# 全パッケージ緑 + serving の既存予測 parity
for p in db features training serving api betting probability; do (cd $p && uv run pytest -q); done
```
