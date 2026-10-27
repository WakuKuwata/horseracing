"""Export already stored local material in one read-only, repeatable transaction."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
from horseracing_training.market_ev import ROWS_SQL
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/157-verified-roi-comparison/snapshot-v2"
AUDIT = ROOT / "artifacts/155-saved-odds-settlement-audit"
QUERIES = {
    "raw": ROWS_SQL,
    "runs": "select p.prediction_run_id::text,p.race_id,p.model_version,p.logic_version,"
        "p.computed_at from prediction_runs p where p.race_id=any(:ids) "
        "and p.model_version='mix-129-nj6' order by p.race_id,p.computed_at,p.prediction_run_id",
    "predictions": "select x.prediction_run_id::text,x.horse_id,x.win_prob::text,x.top2_prob::text,"
        "x.top3_prob::text,x.created_at,x.updated_at "
        "from race_predictions x join prediction_runs p using(prediction_run_id) "
        "where p.race_id=any(:ids) and p.model_version='mix-129-nj6' "
        "order by x.prediction_run_id,x.horse_id",
    "features": "select f.prediction_run_id::text,f.horse_id,f.feature_version,f.features::text "
        "as features_json,f.created_at,f.updated_at from feature_snapshots f "
        "join prediction_runs p using(prediction_run_id) where p.race_id=any(:ids) "
        "and p.model_version='mix-129-nj6' order by f.prediction_run_id,f.horse_id",
    "entries": "select h.race_id,h.horse_id,h.horse_number,h.entry_status,h.odds::text "
        "from race_horses h where h.race_id=any(:ids) order by h.race_id,h.horse_id",
    "results": "select x.race_id,x.horse_id,x.finish_order,x.result_status from race_results x "
        "where x.race_id=any(:ids) order by x.race_id,x.horse_id",
    "races": "select r.race_id,r.race_date::text,r.post_time,r.track_type from races r "
        "where r.race_id=any(:ids) order by r.race_id",
    "model": "select m.model_version,m.feature_version,m.weights_uri,m.calibrator_uri,"
        "m.metrics_summary::text,m.registered_at from model_versions m "
        "where m.model_version='mix-129-nj6'",
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    if OUT.exists():
        raise FileExistsError("Never overwrite a saved snapshot")
    ledger_path = AUDIT / "audit/races.parquet"
    fixed = json.loads((AUDIT / "output-sha256.json").read_text())["files"]
    if sha(ledger_path) != fixed[str(ledger_path.relative_to(ROOT))]:
        raise ValueError("Audit ledger changed")
    ledger = pd.read_parquet(ledger_path)
    ids = sorted(ledger.loc[ledger.archive_full_field_price_verified.fillna(False), "race_id"])
    OUT.mkdir(parents=True)
    engine = create_engine(os.getenv("DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"),
                           hide_parameters=True, echo=False)
    tables = {}
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout='120s'"))
        settings = dict(connection.execute(text(
            "select current_setting('transaction_read_only') as read_only,"
            "current_setting('transaction_isolation') as isolation,"
            "transaction_timestamp()::text as transaction_time,"
            "pg_current_snapshot()::text as snapshot_identity"
        )).mappings().one())
        if settings["read_only"] != "on" or settings["isolation"] != "repeatable read":
            raise ValueError("Read-only snapshot required")
        for name, sql in QUERIES.items():
            frame = pd.read_sql(text(sql), connection, params=dict(
                ids=ids, since="2007-01-01", through="2026-10-25"))
            path = OUT / (name + ".parquet")
            frame.to_parquet(path, index=False)
            tables[name] = dict(path=str(path.relative_to(ROOT)), sha256=sha(path), rows=len(frame), sql=sql)
            print(f"EXPORTED {name}: {len(frame):,}", flush=True)
        connection.rollback()
    engine.dispose()
    (OUT / "manifest.json").write_text(json.dumps(dict(
        started_at=started, finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        settings=settings, tables=tables, candidate_race_ids=ids,
        candidate_ledger_sha256=sha(ledger_path), source_sha256=sha(__file__),
        new_external_collection=False, database_write=False,
    ), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
