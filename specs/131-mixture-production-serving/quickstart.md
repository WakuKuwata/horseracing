# 131 Quickstart

```bash
# 登録(実施済み: mix-129-nj6 は candidate 行あり)
DATABASE_URL=... serving/.venv/bin/python scripts/register_mixture_model_version.py --model-version mix-129-nj6 \
  --display-name "..." --purpose "..." --evidence specs/130-mixture-preweight-walkforward/evidence/walkforward-summary.json --apply

# 単発予測(候補指定・parity 確認用)
cd serving && ../serving/.venv/bin/python -m horseracing_serving predict --race-id <race_id> --model-version mix-129-nj6

# 昇格(override・理由は metrics_summary に残る)
DATABASE_URL=... training/.venv/bin/python -m horseracing_training promote-model --model-version mix-129-nj6 \
  --override-reason "131 override: 130 発走前 walk-forward -0.008969 (CI [-0.01397,-0.00397])" --apply

# 昇格後: 2026 年分を新 active で埋め戻し(既存 run は skip)
cd serving && ../serving/.venv/bin/python -m horseracing_serving predict-backfill --from 2026-01-01 --to 2026-09-06

# 巻き戻し
DATABASE_URL=... training/.venv/bin/python -m horseracing_training promote-model --model-version lgbm-094-cap900 \
  --override-reason "rollback of mix-129-nj6" --apply
```
