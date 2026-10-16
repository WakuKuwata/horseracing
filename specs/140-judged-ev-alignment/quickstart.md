# Quickstart: 140 T011 — migration 0021 のローカル適用と確認

対象: 本番スタック(`localhost:15432` の DB・`scripts/stack.sh` の worker/ops-api/api)。
**順序が正しさに関わる**: migration → 再起動。コードの反映だけを先行させない。

## なぜ今すぐか(レビュー training-token-2・high)

- 本番 DB は `alembic_version = 0020_official_win_payouts` のままで `races.win_odds_official_at` / `win_odds_observed_at` が無い(2026-10-12 時点・読み取り専用で確認)。
- ops worker はジョブごとに `uv run --project <training|serving|betting|live>` を**作業ツリー cwd**で起動し、各 venv は editable install(`_editable_impl_horseracing_db.pth`)でこのツリーを import する。**再起動しなくても subprocess はブランチ 140 の ORM/SQL で動く**。
- したがって 0021 未適用のまま開催日の refresh を 1 回押すと:
  - `serving/pipeline.py` / `live/orchestrate.py` の `session.get(Race)` → UndefinedColumn(predict 失敗)
  - `scrape/upsert.py::update_odds` の `select(Race.win_odds_official_at)` → pending レースの odds ジョブ失敗
  - `training/market_ev.py` ROWS_SQL → expected_return ジョブ全件 FAILED
  - **`apply_final_odds` は `begin_nested` 内で黙って失敗**し、結果は保存されるが単勝は発走前値のまま確定扱い(139 R05 の再発)・来歴も付かない
- 最後のジョブは 10-11 23:12 JST、コード変更は 10-11 23:21 / 10-12 07:39。まだどのジョブも新コードに当たっていない。

## 手順

```bash
cd /Users/kuwatawaku/workspace/horseracing/db
DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing uv run alembic current        # 0020_official_win_payouts
DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing uv run alembic upgrade head   # → 0021_win_odds_provenance
DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing uv run alembic current        # 0021_win_odds_provenance
```

```bash
cd /Users/kuwatawaku/workspace/horseracing
scripts/stack.sh restart worker && scripts/stack.sh restart ops-api && scripts/stack.sh restart api
scripts/stack.sh status
```

## 確認(適用直後)

```sql
select column_name, data_type, is_nullable
from information_schema.columns
where table_name = 'races' and column_name like 'win_odds%';
-- win_odds_official_at | timestamp with time zone | YES
-- win_odds_observed_at | timestamp with time zone | YES
```

```bash
# 新コードが本番 DB を読めること(読み取り専用・何も書かない)
cd /Users/kuwatawaku/workspace/horseracing/training
uv run python - <<'EOF'
from sqlalchemy import create_engine, text
from horseracing_training import market_ev
e = create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
with e.connect() as c:
    c.execute(text("set transaction read only"))
    n = c.execute(text(market_ev._FINAL_ODDS_PROVENANCE_MISSING_SQL),
                  {"since": market_ev.FINAL_ODDS_PROVENANCE_FROM}).scalar_one()
    print("final_odds_provenance_missing =", n)   # 0 を期待(10-13 以降に確定したレースがまだ無い)
EOF
```

## 確認(次の開催日・SC-001 / SC-002)

1. odds ジョブ(refresh)の後: 対象レースの `races.win_odds_official_at`(payload の `official_datetime`・JST→UTC)と `win_odds_observed_at`(取得時刻)が入る。
2. 同じレースの entries を再取込しても 2 列は動かない(SC-001)。
3. その refresh が enqueue した expected_return ジョブ(`ingestion_jobs.job_type='expected_return'`)の `summary`:
   - `provenance.fallback_races = 0`・`provenance.final_odds_provenance_missing = 0`
   - `error_message` が NULL(`provenance: final_odds_provenance_missing=K` が立っていれば 139 の確定値書込が走らなかったレースがある=調べる)
   - stdout の同値は `market-ev-provenance: fallback_rows=0 fallback_races=0 final_odds_provenance_missing=0 proxy_rows_written=0`
4. 新規 `market_ev_predictions` 行の `logic_version` が `;oddsobs=quote` で終わる(SC-002)。
5. 結果取込の後: 確定レースの `win_odds_official_at` が NULL・`win_odds_observed_at` が結果ページの取得時刻(FR-003)。

```sql
select r.race_id, r.win_odds_official_at, r.win_odds_observed_at,
       count(*) filter (where m.logic_version like '%oddsobs=quote') as quote_rows,
       count(*) filter (where m.logic_version like '%oddsobs=proxy') as proxy_rows
from races r join market_ev_predictions m using (race_id)
where r.race_date = current_date and m.model_version = 'mev-ens15-v1'
group by 1, 2, 3 order by 1;
```

## 適用が遅れたとき

適用が **10-13 のレース確定より後**にずれた場合、その間に確定したレースは `apply_final_odds` の来歴を持たず、`final_odds_provenance_missing` が恒久的に >0 で張り付く(すべての expected_return ジョブに `error_message` が立つ)。どちらかを選ぶ:

- `training/src/horseracing_training/market_ev.py` の `FINAL_ODDS_PROVENANCE_FROM` を**実適用日の翌日**に動かす(コメントの「A3 went live」も合わせる)。
- 該当レースの `win_odds_observed_at` を `official_win_payouts.observed_at` から埋める(plan D11 の別作業・139 期の 3,500 レースも同じ手で埋められる)。

## 以後(週ごと)

```bash
cd /Users/kuwatawaku/workspace/horseracing/training
uv run python ../scripts/roi_explore/judged_20261011/score.py --from <sat> --to <sun>   # protocol v2・append-only
```

初回の results で `evaluation_all_days.h_distribution` と `generator.json` の `horizon_pool_minutes` を並べて記録する(plan §4.4・harness-leak-protocol-4)。
