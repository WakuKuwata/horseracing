"""Sanitized local process settings + administrative read-only competition snapshot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import subprocess

from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url

from scripts.roi_explore.db_runtime_166.database import (
    create_readonly_engine,
    readonly_snapshot,
    database_only,
)
from scripts.roi_explore.live_preflight_169.database import _encode
from scripts.roi_explore.prospective_rehearsal_162.store import publish

ROOT = Path(__file__).resolve().parents[3]
INTERVAL_KEYS = ("HORSERACING_NETKEIBA_MIN_INTERVAL_S", "OPS_FETCH_MIN_INTERVAL")
KEYS = (
    *INTERVAL_KEYS,
    "HORSERACING_SHARED_LIMITER",
    "HORSERACING_ROBOTS_CACHE_DIR",
    "DATABASE_URL",
    "DATABASE_URL_OWNER",
)


def summarize(values):
    result = {}
    for key in INTERVAL_KEYS:
        raw = values.get(key)
        if raw is None:
            result[key] = dict(explicit=False, code_default_seconds=1.0)
        else:
            try:
                number = float(raw)
                assert math.isfinite(number) and number >= 0
                result[key] = dict(explicit=True, seconds=number)
            except (ValueError, TypeError, AssertionError, OverflowError):
                result[key] = dict(explicit=True, invalid=True)
    raw = values.get("HORSERACING_SHARED_LIMITER")
    result["shared_limiter_disabled"] = (raw or "").strip().lower() in {
        "0",
        "false",
        "no",
    }
    result["shared_limiter_explicit"] = raw is not None
    directory = values.get("HORSERACING_ROBOTS_CACHE_DIR")
    result["robots_cache"] = dict(
        explicit=directory is not None,
        enabled=directory is None or bool(directory.strip()),
        matches_repo_default=directory is None
        or directory == str(ROOT / "artifacts/robots_cache"),
    )
    for key in ("DATABASE_URL", "DATABASE_URL_OWNER"):
        raw = values.get(key)
        if not raw:
            result[key] = dict(configured=False)
            continue
        try:
            url = make_url(raw)
            # Never return username/password, arbitrary URL options or original URL strings.
            result[key] = dict(
                configured=True,
                host=url.host,
                port=url.port,
                database=url.database,
                matches_confirmed_local_database=url.host
                in ("127.0.0.1", "localhost", "::1")
                and url.port == 15432
                and url.database == "horseracing",
            )
        except Exception:
            result[key] = dict(configured=True, invalid=True)
    return result


def processes():
    raw = subprocess.run(
        ["/bin/ps", "-axww", "-o", "pid=,comm="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    results = []
    for line in raw.splitlines():
        match = re.match(r"\s*(\d+)\s+(.+)$", line)
        if not match:
            continue
        pid, executable = match.groups()
        if "python" not in executable.lower() or int(pid) == os.getpid():
            continue
        command = subprocess.run(
            ["/bin/ps", "-ww", "-p", pid, "-o", "args="], capture_output=True, text=True
        ).stdout
        service = (
            "api"
            if "/api/.venv/" in executable or "horseracing_api" in command
            else (
                "ops"
                if "/ops/.venv/" in executable or "horseracing_ops" in command
                else "other_potential_source_caller"
                if any(
                    x in command
                    for x in (
                        "horseracing_scrape",
                        "horseracing_live",
                        "netkeiba",
                        "chaos-capture",
                    )
                )
                else None
            )
        )
        if service is None:
            continue
        env = subprocess.run(
            ["/bin/ps", "eww", "-p", pid, "-o", "command="],
            capture_output=True,
            text=True,
        ).stdout
        values = {}
        for key in KEYS:
            match = re.search(r"(?:^|\s)" + key + r"=([^\s]*)", env)
            if match:
                values[key] = match[1]
        results.append(
            dict(
                pid=int(pid),
                service=service,
                settings=summarize(values),
                worker_argument_present=bool(re.search(r"\bworker\b", command)),
                schedule_argument_present=bool(
                    re.search(r"\bschedul(?:e|er)\b", command)
                ),
                raw_environment_or_command_saved=False,
                initial_environment_observed=bool(env),
            )
        )
    return results


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    process_rows = processes()
    saved = []
    for name in (
        ".env",
        "api/.env",
        "ops/.env",
        "deploy/.env",
        "config/.env.manual-observer",
    ):
        path = ROOT / name
        if path.is_file() and not path.is_symlink():
            saved.append(
                dict(
                    path=name,
                    settings=summarize(dotenv_values(path, interpolate=False)),
                    original_file_or_hash_saved=False,
                )
            )
    with database_only() as boundary:
        engine, audit = create_readonly_engine()
        try:
            with readonly_snapshot(engine, audit) as (conn, proof):
                identity = (
                    conn.execute(
                        text(
                            "SELECT current_database() AS database_name,"
                            "(SELECT oid::text FROM pg_database WHERE datname=current_database()) AS database_oid,"
                            "(pg_control_system()).system_identifier::text AS system_identifier"
                        )
                    )
                    .mappings()
                    .one()
                )
                jobs = [
                    dict(row)
                    for row in conn.execute(
                        text(
                            "SELECT job_type,status::text AS status,"
                            "count(*) AS jobs FROM ingestion_jobs WHERE lower(status::text) IN ('running','queued') "
                            "GROUP BY job_type,status ORDER BY job_type,status"
                        )
                    ).mappings()
                ]
                sessions = [
                    dict(row)
                    for row in conn.execute(
                        text(
                            "SELECT usename,state,count(*) AS sessions "
                            "FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() "
                            "GROUP BY usename,state ORDER BY usename,state"
                        )
                    ).mappings()
                ]
                locks = [
                    dict(row)
                    for row in conn.execute(
                        text(
                            "SELECT mode,granted,count(*) AS locks "
                            "FROM pg_locks WHERE relation=to_regclass('public.fetch_throttle_state') "
                            "GROUP BY mode,granted ORDER BY mode,granted"
                        )
                    ).mappings()
                ]
                throttle = [
                    dict(row)
                    for row in conn.execute(
                        text(
                            "SELECT domain,next_allowed_at,"
                            "blocked_until,block_reason,updated_at FROM fetch_throttle_state "
                            "WHERE domain='netkeiba.com'"
                        )
                    ).mappings()
                ]
        finally:
            engine.dispose()
    result = _encode(
        dict(
            checked_at=dt.datetime.now(dt.UTC),
            scope="observed_local_processes_and_current_db_snapshot",
            processes=process_rows,
            saved_configuration=saved,
            identity=dict(identity),
            proof=proof,
            jobs=jobs,
            sessions=sessions,
            throttle_relation_locks=locks,
            throttle=throttle,
            network_boundary=boundary,
            database_write=False,
            robots_request=False,
            race_data_collection=False,
            raw_environment_or_command_saved=False,
            running_processes_restarted=False,
            configuration_changed=False,
            future_contention_unconfirmed=True,
            operator_policy_approval_confirmed=False,
        )
    )
    publish(output / "result.json", result)
    publish(output / "database-audit.json", audit)
    return dict(
        processes=len(process_rows),
        active_or_queued_job_groups=len(jobs),
        database_write=False,
        race_data_collection=False,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.out)))
    except Exception as error:
        print(
            json.dumps(
                dict(
                    status="STOPPED",
                    error_class=type(error).__name__,
                    private_details_not_printed=True,
                )
            )
        )
        raise SystemExit(1) from None
