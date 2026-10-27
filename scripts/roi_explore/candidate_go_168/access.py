"""Inspect existing access state without reserving slots, refreshing robots or changing config."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.robotparser import RobotFileParser

from sqlalchemy import text
from scripts.roi_explore.db_runtime_166.database import (
    create_readonly_engine,
    readonly_snapshot,
    database_only,
)
from scripts.roi_explore.prospective_rehearsal_162.store import publish
from .snapshot import encode, ROOT

ORIGIN = "https://race.netkeiba.com"
URL = ORIGIN + "/api/api_get_jra_odds.html"
UA = "horseracing-live/0.1 (personal use; contact via repo)"


def inspect_robots(*, now):
    raw_dir = os.environ.get("HORSERACING_ROBOTS_CACHE_DIR")
    directory = (
        ROOT / "artifacts/robots_cache"
        if raw_dir is None
        else Path(raw_dir.strip())
        if raw_dir.strip()
        else None
    )
    evidence = dict(
        origin=ORIGIN,
        source="direct_read_of_existing_cache_only",
        ttl_seconds=86400,
        fresh_now=False,
        valid_for_selected_launch=None,
        selected_launch_at=None,
    )
    if directory is None:
        return dict(evidence, status="CACHE_DISABLED")
    path = directory.resolve() / (
        hashlib.sha256(ORIGIN.encode()).hexdigest()[:20] + ".json"
    )
    evidence["path"] = str(path)
    if not path.exists():
        return dict(evidence, status="CACHE_MISSING")
    body = path.read_bytes()
    evidence["sha256"] = hashlib.sha256(body).hexdigest()
    try:
        doc = json.loads(body)
        stamp = dt.datetime.fromisoformat(doc["fetched_at"])
        if (
            stamp.tzinfo is None
            or doc["schema_version"] != 1
            or doc["origin"] != ORIGIN
        ):
            raise ValueError("cache_metadata_invalid")
        age = (now - stamp).total_seconds()
        rules = base64.b64decode(doc.get("body") or "", validate=True)
        if doc["outcome"] == "rules":
            parser = RobotFileParser()
            parser.parse(rules.decode("utf-8", errors="replace").splitlines())
            allowed = parser.can_fetch(UA, URL)
        elif doc["outcome"] == "absent":
            allowed = True
        else:
            raise ValueError("cache_outcome_invalid")
        evidence.update(
            original_fetched_at=stamp.astimezone(dt.UTC).isoformat(),
            age_seconds=age,
            expires_at=(stamp + dt.timedelta(seconds=86400)).isoformat(),
            parsed_rules_allow_odds_api=allowed,
            fresh_now=0 <= age < 86400,
            status="FRESH_NOW_ONLY" if 0 <= age < 86400 else "STALE_OR_FUTURE_NOW",
        )
    except (KeyError, TypeError, ValueError):
        evidence["status"] = "CACHE_INVALID"
    return evidence


def capture_access(output):
    root = Path(output)
    now = dt.datetime.now(dt.UTC)
    result = dict(
        as_of_utc=now.isoformat(),
        new_collection=False,
        database_write=False,
        cache_refreshed=False,
        slot_reserved=False,
        cooldown_cleared=False,
        robots=inspect_robots(now=now),
        live_database_url_configured=bool(os.getenv("DATABASE_URL")),
        live_role_confirmed=False,
        operator_available_interval=None,
        source_interval_seconds=None,
        launch_concurrency_confirmed=False,
    )
    try:
        interval = float(os.getenv("HORSERACING_NETKEIBA_MIN_INTERVAL_S") or "1")
        if not math.isfinite(interval):
            raise ValueError("nonfinite_interval")
        result["source_interval_seconds"] = max(1.0, interval)
    except ValueError:
        result["source_interval_status"] = "MALFORMED_ENVIRONMENT_NOT_GO"
    engine, audit = create_readonly_engine()
    audit["phase"] = "existing_access_state_readonly"
    try:
        with database_only() as network:
            with readonly_snapshot(engine, audit) as (conn, proof):
                result["readonly_proof"] = proof
                result["throttle_rows"] = encode(
                    [
                        dict(r)
                        for r in conn.execute(
                            text(
                                "SELECT domain,next_allowed_at,blocked_until,block_reason,updated_at "
                                "FROM fetch_throttle_state ORDER BY domain"
                            )
                        )
                        .mappings()
                        .all()
                    ]
                )
            result["network_boundary"] = network
        rows = [r for r in result["throttle_rows"] if r["domain"] == "netkeiba.com"]
        result["canonical_throttle_key"] = "netkeiba.com"
        result["canonical_throttle_row_count"] = len(rows)
        result["future_slot_available_at_launch"] = None
        if len(rows) == 1:
            row = rows[0]
            result["canonical_throttle_state_now"] = dict(
                blocked_now=(
                    dt.datetime.fromisoformat(row["blocked_until"]) > now
                    if row["blocked_until"]
                    else False
                ),
                next_allowed_at=row["next_allowed_at"],
                state_not_a_future_slot_reservation=True,
            )
        else:
            result["canonical_throttle_state_now"] = "MISSING_OR_AMBIGUOUS"
        publish(root / "access.json", result)
        publish(root / "access-database-audit.json", audit)
        return result
    finally:
        engine.dispose()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    data = capture_access(args.out)
    print(
        {
            k: data[k]
            for k in (
                "robots",
                "live_database_url_configured",
                "canonical_throttle_state_now",
            )
        }
    )
