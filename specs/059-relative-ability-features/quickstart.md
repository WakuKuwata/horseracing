# Quickstart: relative_ability features (059)

前提: worktree `059-relative-ability-features`、local DB(`DATABASE_URL=postgresql+psycopg://
aiuma:aiuma@localhost:15432/horseracing`)、features-013 が現行。

## 1. Build & bit-parity(US1)

```bash
# in-memory と materialized で新群込み bit 一致を確認
uv run --project features pytest -q            # unit(LOO 意味論/rank/NaN)+ leak-guard + bit-parity
uv run --project features python -m horseracing_features materialize \
  --out ../artifacts/features.parquet          # features-014 で再生成(旧 parquet は fail-closed)
```

期待: features unit 緑、bit-parity 緑(check_exact)、leak-guard 緑(結果/オッズ/同日改変で不変)。

## 2. 事前登録採用ゲート(US2, binary feature-eval)

```bash
uv run --project training python -m horseracing_training feature-eval \
  --drop-groups relative_ability
```

期待(spike 13列版の再現目安): mean win LogLoss 改善・mean ECE 非悪化・過半 fold 勝ち →
`primary_pass=True`。数値は実 DB のゲートが公式値。

## 3. 本番 pl_topk overlap 検証(US2, 必須)

```bash
uv run --project training python -m horseracing_training model-eval \
  --objective pl_topk --calibration isotonic --target-encode jockey_id,trainer_id
```

期待: 候補 win LogLoss < **0.21615**(lgbm-056)、top2/top3 非悪化。ここで overlap リスクを実測。
縮んで超えない場合はユーザー判断(採用見送り or 023/039/056 型の総合判断)。

## 4. 採用時: lgbm-057 学習 → active 昇格

```bash
# vectorized pl_topk + bulk eval loader で ~20 分/回(nohup + 監視推奨=DB 再起動耐性)
uv run --project training python -m horseracing_training train-evaluate \
  --objective pl_topk --calibration isotonic --target-encode jockey_id,trainer_id \
  --model-version lgbm-057
# 採用ゲート PASS を確認 → model_versions で lgbm-057 active / lgbm-056 retired
```

## 5. Serving 疎通

```bash
uv run --project serving python -m horseracing_serving predict --race-id <rid>
# ログに feature_version: features-014 / feature cols 数増(+13)を確認
```

## 完了条件(SC 対応)

- SC-001 bit-parity 緑 / SC-002 leak-guard 緑 / SC-003 feature-eval 再現 /
  SC-004 pl_topk が 0.21615 超え / SC-005 全パッケージ緑・ruff・drift・migration head 不変。
