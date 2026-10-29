# 175 quickstart — 検証手順

前提: ローカル DB(0022 適用済み)・`live/.venv`・`artifacts/market_ev/mev-ens15-v1`・`mev-binary-v2`。

## 1. 単体・統合テスト

```bash
cd eval && uv run pytest tests/unit/test_decision_quote_scoring.py -q
cd ../live && uv run pytest tests/unit/test_near_post_score.py tests/integration/test_near_post_score_db.py -q
cd ../api && uv run pytest tests/unit/test_prospective_funnel_parity_175.py -q
cd ../features && uv run pytest tests/unit/test_decision_quotes_leak_guard.py -q
```

期待: 全部緑。eval は funnel 合計・同着・同期差 0・決定論・帯境界・`decide` 結線を含む。

## 2. T0 parquet 往復(実 DB・読み取りのみ)

```bash
cd live && uv run python -c "
import datetime, pandas as pd
from sqlalchemy import create_engine
from horseracing_live import near_post as np_
e = create_engine('postgresql+psycopg://…')
with e.connect() as c:
    df = np_.build_day_features(c, datetime.date(2026,10,25))
df.frame.to_parquet('/tmp/f.parquet', index=False)
print(np_.frame_sha256(pd.read_parquet('/tmp/f.parquet')) == df.sha256)"
```

期待: `True`。`False` なら research D10 のとおり pickle に切替えて contracts/cli.md を更新。

## 3. 10/31 の試運転の採点(SC-001)

```bash
scripts/near_post_ops.sh score 2026-10-31 2026-10-31
```

期待: 標準出力の先頭に `power_note`。funnel の合計=行数。`columns` の `n_bets`/`return_yen` が次の SQL と一致:

```sql
-- 120% 列の目印と払戻(手計算)
SELECT q.race_id, p->>'horse_number' AS hn, (p->>'ev_ens')::float AS ev,
       coalesce(w.payout_yen, 0) AS ret
FROM decision_quotes q, jsonb_array_elements(q.pred) p
LEFT JOIN official_win_payouts w ON w.race_id = q.race_id AND w.horse_number = (p->>'horse_number')::int
WHERE q.race_id IN (SELECT race_id FROM races WHERE race_date = '2026-10-31')
  AND (p->>'ev_ens')::float > 1.2;
```

## 4. 恒等候補(SC-003)

`candidates.json` に一時的に `kind: ensemble_dir`・`artifact_path` を現行の `artifacts/market_ev/mev-ens15-v1` にした候補を登録(登録日=今日)して:

```bash
scripts/near_post_ops.sh score 2026-10-31 2026-11-01 identity-ens15
```

期待: `diff == 0.0`・`diff_ci == [0.0, 0.0]`・`n_races_not_reproducible` の件数が報告される(閾値なし)。終了後に一時候補は削除(登録履歴は git の diff に残る)。

## 5. 読み取り専用(SC-004)

`DATABASE_URL` を `hr_manual_observer` の接続文字列にして §3 を再実行 → 完走。前後で `SELECT count(*) FROM decision_quotes` 不変。

## 6. 拒否経路

- `candidates.json` の `artifact_sha256` を 1 文字変える → 終了 2。
- `specs/174/display-update-rule.json` を一時改変 → 終了 2(戻す)。
- `--out` に既存ファイル → 終了 2。
