"""Read existing stored predictions/quotes and official settlement; never read closing odds."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/158-prerace-roi-comparison/snapshot"
AUDIT = ROOT / "artifacts/155-saved-odds-settlement-audit"
QUERIES = {
    "races": "select race_id,race_date::text,post_time,track_type from races "
        "where race_id=any(:ids) order by race_id",
    "entries": "select race_id,horse_id,horse_number,entry_status from race_horses "
        "where race_id=any(:ids) order by race_id,horse_id",
    "results": "select race_id,horse_id,finish_order,result_status from race_results "
        "where race_id=any(:ids) order by race_id,horse_id",
    "payouts": "select race_id,horse_number,payout_yen,source,observed_at,html_sha256 "
        "from official_win_payouts where race_id=any(:ids) order by race_id,horse_number",
    "mev": "select race_id,horse_id,horse_number,model_version,win_prob::text,odds_used::text,"
        "expected_return::text,odds_observed_at,result_pending_at_compute,logic_version,"
        "run_id::text,computed_at,booster,booster_sha256 from market_ev_predictions "
        "where race_id=any(:ids) and model_version='mev-ens15-v1' order by race_id,horse_id",
    "runs": "select prediction_run_id::text,race_id,model_version,logic_version,computed_at,"
        "created_at,updated_at from prediction_runs where race_id=any(:ids) "
        "and model_version='mix-129-nj6' order by race_id,computed_at,prediction_run_id",
    "predictions": "select x.prediction_run_id::text,x.horse_id,x.win_prob::text,"
        "x.top2_prob::text,x.top3_prob::text,x.created_at,x.updated_at from race_predictions x "
        "join prediction_runs p using(prediction_run_id) where p.race_id=any(:ids) "
        "and p.model_version='mix-129-nj6' order by x.prediction_run_id,x.horse_id",
    "features": "select f.prediction_run_id::text,f.horse_id,f.feature_version,"
        "f.features::text as features_json,f.created_at,f.updated_at from feature_snapshots f "
        "join prediction_runs p using(prediction_run_id) where p.race_id=any(:ids) "
        "and p.model_version='mix-129-nj6' order by f.prediction_run_id,f.horse_id",
    "model": "select model_version,feature_version,weights_uri,calibrator_uri,"
        "metrics_summary::text,registered_at from model_versions where model_version='mix-129-nj6'",
    "chaos": "select chaos_snapshot_id::text,race_id,captured_at,created_at,source,"
        "seconds_to_post,capture_strength,field::text as field_json,n,content_digest,status,"
        "void_reason,capture_trigger,capture_policy_version from chaos_snapshots "
        "where race_id=any(:ids) order by race_id",
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    if OUT.exists():
        raise FileExistsError("Never overwrite exported material")
    path = AUDIT / "audit/races.parquet"
    expected = json.loads((AUDIT / "output-sha256.json").read_text())["files"]
    if sha(path) != expected[str(path.relative_to(ROOT))]:
        raise ValueError("Settlement coverage audit changed")
    # Read quality keys only; the ledger's closing-price diagnostics never enter a decision.
    ledger = pd.read_parquet(path, columns=["race_id", "field_status", "payout_status"])
    ids = sorted(ledger.loc[ledger.field_status.eq("complete") & ledger.payout_status.eq("consistent"), "race_id"])
    OUT.mkdir(parents=True)
    engine = create_engine(os.getenv("DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"),
                           echo=False, hide_parameters=True)
    tables = {}
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout='120s'"))
        settings = dict(connection.execute(text(
            "select current_setting('transaction_read_only') as read_only,"
            "current_setting('transaction_isolation') as isolation,"
            "transaction_timestamp()::text as transaction_time,pg_current_snapshot()::text as snapshot_identity"
        )).mappings().one())
        if settings["read_only"] != "on" or settings["isolation"] != "repeatable read":
            raise ValueError("Unsafe snapshot")
        for name, sql in QUERIES.items():
            frame = pd.read_sql(text(sql), connection, params=dict(ids=ids))
            path = OUT / (name + ".parquet")
            frame.to_parquet(path, index=False)
            tables[name] = dict(path=str(path.relative_to(ROOT)), rows=len(frame), sha256=sha(path), sql=sql)
            print(f"EXPORTED {name}: {len(frame):,}", flush=True)
        connection.rollback()
    engine.dispose()
    (OUT / "manifest.json").write_text(json.dumps(dict(
        started_at=started, finished_at=dt.datetime.now(dt.timezone.utc).isoformat(), settings=settings,
        candidate_race_ids=ids, candidate_ledger_sha256=sha(AUDIT / "audit/races.parquet"),
        tables=tables, source_sha256=sha(__file__), new_external_collection=False, database_write=False,
        closing_odds_selected=False,
    ), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
