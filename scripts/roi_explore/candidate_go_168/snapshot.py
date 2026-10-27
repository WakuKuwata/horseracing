"""Saved metadata only: consistent read-only DB snapshot, never observe/refresh/infer."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from sqlalchemy import text
from scripts.roi_explore.db_runtime_166.database import (
    create_readonly_engine,
    readonly_snapshot,
    database_only,
)
from scripts.roi_explore.model_connection_163.clock import RealClock
from scripts.roi_explore.prospective_rehearsal_162.store import publish

ROOT = Path(__file__).resolve().parents[3]
JST = dt.timezone(dt.timedelta(hours=9))
FUTURE = "(r.race_date >= :today OR r.post_time >= :now)"
QUERIES = {
    "coverage": """SELECT count(*) AS races_total, min(race_date) AS first_race_date,
max(race_date) AS last_race_date, max(post_time) AS last_post_time,
count(*) FILTER (WHERE race_date IS NULL) AS unknown_dates,
count(*) FILTER (WHERE race_date >= :today) AS races_today_or_later,
count(*) FILTER (WHERE post_time > :now) AS future_post_rows,
count(*) FILTER (WHERE race_date >= :today AND post_time IS NULL) AS upcoming_missing_post,
current_timestamp AS snapshot_database_time FROM races""",
    "date_coverage": """SELECT race_date, count(*) AS races,
count(post_time) AS known_post, min(post_time) AS first_post, max(post_time) AS last_post
FROM races WHERE race_date >= :today GROUP BY race_date ORDER BY race_date""",
    "upcoming_races": f"""SELECT r.race_id,r.race_date,r.post_time,r.venue_code,r.race_number,
r.race_name,r.track_type,r.race_status,r.created_at,r.updated_at,
(SELECT count(*) FROM race_horses h WHERE h.race_id=r.race_id) AS entry_rows,
(SELECT count(*) FROM race_horses h WHERE h.race_id=r.race_id AND h.entry_status='started') AS started_rows,
(SELECT count(*) FROM race_results s WHERE s.race_id=r.race_id) AS result_rows
FROM races r WHERE {FUTURE} ORDER BY r.post_time ASC NULLS LAST,r.race_id""",
    "upcoming_entries": f"""SELECT h.race_id,h.horse_id,h.horse_number,h.entry_status
FROM race_horses h JOIN races r ON r.race_id=h.race_id
WHERE {FUTURE} ORDER BY r.post_time ASC NULLS LAST,r.race_id,h.horse_number,h.horse_id""",
    "latest_saved_day": """SELECT r.race_date,count(*) AS races,
min(r.post_time) AS first_post,max(r.post_time) AS last_post,
count(*) FILTER (WHERE EXISTS (SELECT 1 FROM race_results s WHERE s.race_id=r.race_id)) AS races_with_results
FROM races r WHERE r.race_date=(SELECT max(race_date) FROM races)
GROUP BY r.race_date""",
    "recent_saved_reference": """SELECT r.race_id,r.race_date,r.post_time,r.race_name,r.track_type,
(SELECT count(*) FROM race_horses h WHERE h.race_id=r.race_id AND h.entry_status='started') AS started_rows,
(SELECT count(*) FROM race_results s WHERE s.race_id=r.race_id) AS result_rows
FROM races r WHERE r.race_date=(SELECT max(race_date) FROM races)
ORDER BY r.post_time,r.race_id LIMIT 3""",
    "relations": """SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'
AND tablename IN ('races','race_horses','race_results','horses','id_mappings',
'official_win_payouts','fetch_throttle_state','ingestion_jobs') ORDER BY tablename""",
}
PRIVILEGES = """SELECT has_table_privilege(current_user,:relation,'SELECT') AS can_read,
has_table_privilege(current_user,:relation,'INSERT') AS can_insert,
has_table_privilege(current_user,:relation,'UPDATE') AS can_modify"""
THROTTLE = """SELECT domain,next_allowed_at,blocked_until,block_reason,updated_at
FROM fetch_throttle_state WHERE domain=:domain"""


def encode(value):
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            raise ValueError("database_timestamp_naive")
        return value.astimezone(dt.UTC).isoformat(timespec="microseconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def capture(output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    clock = RealClock()
    asof = dt.datetime.fromisoformat(clock.now())
    params = dict(now=asof, today=asof.astimezone(JST).date())
    engine, audit = create_readonly_engine()
    audit["phase"] = "saved_metadata_inventory"
    data = dict(
        mode="saved_candidate_go",
        new_collection=False,
        database_write=False,
        actual_db_access=True,
        model_inference=False,
        prices_or_prediction_values_read=False,
        as_of_utc=asof.isoformat(),
        as_of_jst=asof.astimezone(JST).isoformat(),
        live_database_url_configured=bool(os.getenv("DATABASE_URL")),
        connection_source="environment"
        if os.getenv("DATABASE_URL")
        else "166_readonly_fallback",
    )
    try:
        with database_only() as network:
            with readonly_snapshot(engine, audit) as (conn, proof):
                data["readonly_proof"] = proof
                for name, sql in QUERIES.items():
                    data[name] = encode(
                        [
                            dict(r)
                            for r in conn.execute(text(sql), params).mappings().all()
                        ]
                    )
                names = {r["tablename"] for r in data["relations"]}
                data["audited_connection_capabilities"] = {
                    name: dict(
                        conn.execute(text(PRIVILEGES), dict(relation="public." + name))
                        .mappings()
                        .one()
                    )
                    for name in sorted(names)
                }
                data["throttle"] = (
                    encode(
                        [
                            dict(r)
                            for r in conn.execute(
                                text(THROTTLE), dict(domain="https://race.netkeiba.com")
                            )
                            .mappings()
                            .all()
                        ]
                    )
                    if "fetch_throttle_state" in names
                    else None
                )
                data["read_finished_at"] = clock.now()
            data["network_boundary"] = network
        if network["attempts"]:
            raise ValueError("network_was_attempted")
        publish(root / "snapshot.json", data)
        publish(root / "database-audit.json", audit)
        publish(
            root / "clock.json",
            dict(simulated_clock=False, observations=clock.observations),
        )
        return data
    finally:
        engine.dispose()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = capture(args.out)
    print({k: result[k] for k in ("as_of_jst", "coverage", "connection_source")})
