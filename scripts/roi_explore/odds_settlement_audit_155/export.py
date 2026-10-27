"""One consistent READ ONLY database snapshot; no fetch, repair, or model execution."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/155-saved-odds-settlement-audit"
FROM, THROUGH = "2007-01-01", "2026-10-26"
WINDOW = "r.race_date between :date_from and :date_through"
QUERIES = {
    "races": f"select r.race_id,r.race_date::text,r.track_type,r.race_status,r.post_time,"
        f"r.win_odds_official_at,r.win_odds_observed_at,r.created_at,r.updated_at from races r where {WINDOW} order by r.race_id",
    "horses": f"select h.race_id,h.horse_id,h.horse_number,h.entry_status,h.odds::text as odds,h.popularity,"
        f"h.created_at,h.updated_at from race_horses h join races r using(race_id) where {WINDOW} order by h.race_id,h.horse_id",
    "results": f"select x.race_id,x.horse_id,x.finish_order,x.result_status,x.created_at,x.updated_at "
        f"from race_results x join races r using(race_id) where {WINDOW} order by x.race_id,x.horse_id",
    "payouts": f"select p.race_id,p.horse_number,p.payout_yen,p.source,p.observed_at,p.html_sha256,p.created_at,p.updated_at "
        f"from official_win_payouts p join races r using(race_id) where {WINDOW} order by p.race_id,p.horse_number",
    "mev": f"select p.race_id,p.horse_id,p.model_version,p.horse_number,p.win_prob::text as win_prob,"
        f"p.odds_used::text as odds_used,p.expected_return::text as expected_return,p.odds_observed_at,"
        f"p.result_pending_at_compute,p.logic_version,p.run_id::text,p.computed_at,p.booster,p.booster_sha256 "
        f"from market_ev_predictions p join races r using(race_id) where {WINDOW} order by p.race_id,p.model_version,p.horse_id",
    "picks": f"select p.pick_id::text,p.race_id,p.horse_id,p.horse_number,p.rule_id,p.kind,p.void_reason,"
        f"p.odds_used::text as odds_used,p.odds_observed_at,p.post_time,p.seconds_to_post,p.result_pending_at_compute,"
        f"p.field_digest,p.run_id::text,p.computed_at,p.logic_version,p.ensemble_model_version "
        f"from attention_picks p join races r using(race_id) where {WINDOW} order by p.computed_at,p.pick_id",
    "scans": f"select p.race_id,p.rule_set_version,p.run_id::text,p.computed_at,p.post_time,"
        f"p.result_pending_at_compute,p.field_digest,p.n_picks from attention_race_scans p join races r using(race_id) "
        f"where {WINDOW} order by p.race_id,p.rule_set_version",
    "chaos": f"select p.chaos_snapshot_id::text,p.race_id,p.captured_at,p.source,p.seconds_to_post,p.capture_strength,"
        f"p.field::text as field_json,p.n,p.content_digest,p.status,p.void_reason,p.capture_trigger,p.capture_policy_version "
        f"from chaos_snapshots p join races r using(race_id) where {WINDOW} order by p.race_id",
    "recommendations": f"select p.recommendation_id::text,p.prediction_run_id::text,p.race_id,p.selection::text as selection_json,"
        f"p.market_odds_used::text as odds_used,p.is_estimated_odds,p.computed_at,p.logic_version "
        f"from recommendations p join races r using(race_id) where {WINDOW} and p.bet_type='win' order by p.race_id,p.recommendation_id",
    "id_mappings": "select entity_type,source,source_id,canonical_id,mapping_status,resolved_at "
        "from id_mappings where entity_type='horse' order by source,source_id,canonical_id",
}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def export(out=OUT):
    destination = out / "snapshot"
    if destination.exists():
        raise FileExistsError("Snapshot already exists; never overwrite")
    destination.mkdir(parents=True)
    url = os.getenv("DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
    engine = create_engine(url, hide_parameters=True, echo=False)
    start = dt.datetime.now(dt.timezone.utc).isoformat()
    records = {}
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '120s'"))
        settings = dict(connection.execute(text("select current_setting('transaction_read_only') as read_only,"
            "current_setting('transaction_isolation') as isolation,transaction_timestamp()::text as transaction_time,"
            "pg_current_snapshot()::text as snapshot_identity")).mappings().one())
        if settings["read_only"] != "on" or settings["isolation"] != "repeatable read":
            raise ValueError("Database snapshot is not consistent/read-only")
        for name, sql in QUERIES.items():
            frame = pd.read_sql(text(sql), connection, params=dict(date_from=FROM,date_through=THROUGH))
            path = destination / (name + ".parquet")
            frame.to_parquet(path, index=False)
            records[name] = dict(rows=len(frame), path=str(path.relative_to(ROOT)), sha256=sha(path), sql=sql)
            print(f"EXPORTED {name}: {len(frame):,}", flush=True)
        connection.rollback()
    engine.dispose()
    document = dict(window=dict(date_from=FROM,date_through=THROUGH),started_at=start,
        finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),settings=settings,tables=records,
        source_sha256=sha(__file__), new_collection=False, database_write=False)
    (destination / "manifest.json").write_text(json.dumps(document,ensure_ascii=False,indent=2)+"\n")
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--out",type=Path,default=OUT)
    args = parser.parse_args()
    export(args.out)
