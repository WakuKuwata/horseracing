"""Preflight only. No path in this module starts observation or refreshes source data."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from sqlalchemy import text

from .access import inspect_robots, throttle_check
from .config import ROOT, load_profile, profile_environment
from .database import (
    inventory_database,
    native_readonly_probe,
    collect_catalog,
    collect_permissions,
    evaluate_permissions,
    THROTTLE_SQL,
    _encode,
)
from scripts.roi_explore.db_runtime_166.database import database_only
from scripts.roi_explore.prospective_rehearsal_162.store import publish

MODE = "live_preflight_only"


def _pins():
    paths = list(Path(__file__).parent.glob("*.py"))
    paths += [
        ROOT / "specs/169-live-preflight/evidence/permission-contract.json",
        ROOT / "config/manual-observer.env.example",
    ]
    paths += [
        ROOT / name
        for name in (
            "scripts/roi_explore/db_runtime_166/database.py",
            "scripts/roi_explore/manual_observation_165/adapters.py",
            "scripts/roi_explore/manual_observation_165/features.py",
            "scripts/roi_explore/manual_observation_165/cli.py",
            "scripts/roi_explore/manual_observation_165/control.py",
            "scripts/roi_explore/manual_observation_165/session.py",
            "scripts/roi_explore/manual_observation_165/settlement.py",
            "db/src/horseracing_db/session.py",
            "features/src/horseracing_features/loader.py",
            "training/src/horseracing_training/market_ev.py",
            "live/src/horseracing_live/chaos_politeness.py",
            "scrape/src/horseracing_scrape/politeness.py",
            "scrape/src/horseracing_scrape/robots_cache.py",
            "scrape/src/horseracing_scrape/fetch.py",
        )
    ]
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def _finish(root, result, audits, pins):
    publish(root / "result.json", result)
    publish(root / "database-audits.json", audits)
    publish(root / "source-pins.json", pins)
    publish(
        root / "manifest.json",
        dict(
            mode=MODE,
            can_collect=False,
            files={
                str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*"))
                if p.is_file()
            },
        ),
    )
    return result


def _newroot(output):
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    return root


def check(profile_path, output, *, launch_at=None):
    root = _newroot(output)
    pins = _pins()
    now = dt.datetime.now(dt.UTC)
    profile = load_profile(profile_path)
    report = dict(
        mode=MODE,
        scope="explicit_live_profile_only",
        checked_at=now.isoformat(),
        state="NO_GO",
        can_collect=False,
        new_collection=False,
        database_write=False,
        live_plan_created=False,
        model_inference=False,
        observe_invocations=0,
        role_grants_applied=False,
        cache_refreshed=False,
        slot_reserved=False,
        quote_max_age_seconds=1800,
        both_predictions_durable_before_decision_required=True,
        profile=profile.public,
        configuration_problems=profile.problems,
        native_profile_connection="NOT_RUN_CONFIGURATION_BLOCKED",
        native_default_measured=False,
        permission_check=None,
        launch_only_unconfirmed=[
            "target_race_and_full_fresh_card",
            "operator_available_interval",
            "actual_request_url_robots_and_source_slot",
            "launch_time_clock_power_sleep_storage",
            "quote_age_at_decision",
            "both_durable_receipts_before_decision",
            "fresh_before_after_final_guards",
            "decision_and_seal_inside_fixed_window",
            "future_official_raw_results_all_winner_payouts",
        ],
    )
    audits = []
    rows = []
    with database_only() as boundary:
        if not profile.problems:
            try:
                with (
                    profile_environment(profile),
                    native_readonly_probe(profile) as (conn, identity, audit),
                ):
                    audits.append(audit)
                    catalog = collect_catalog(conn)
                    role = next(
                        (
                            r
                            for r in catalog["non_builtin_roles"]
                            if r["rolname"] == identity["current_login"]
                        ),
                        None,
                    )
                    permissions = collect_permissions(conn, identity["current_login"])
                    evaluation = evaluate_permissions(
                        role,
                        permissions,
                        identity=identity,
                        native_default_measured=True,
                    )
                    if (
                        identity["current_login"] != profile.public["endpoint"]["login"]
                        or identity["database_name"]
                        != profile.public["endpoint"]["database"]
                    ):
                        evaluation["blocking_reasons"].append(
                            "authenticated_identity_differs_from_profile"
                        )
                        evaluation["status"] = "BLOCKED"
                    report.update(
                        native_profile_connection="READONLY_QUERY_SUCCEEDED",
                        native_default_measured=True,
                        native_identity=identity,
                        catalog=catalog,
                        permissions=permissions,
                        permission_check=evaluation,
                    )
                    if (
                        "fetch_throttle_state" in permissions["tables"]
                        and permissions["tables"]["fetch_throttle_state"]["metadata"]
                        is not None
                    ):
                        rows = _encode(
                            [
                                dict(r)
                                for r in conn.execute(
                                    text(THROTTLE_SQL), dict(key="netkeiba.com")
                                ).mappings()
                            ]
                        )
            except Exception as error:
                # DB exceptions may contain DSNs, credentials or query parameter values.
                report.update(
                    native_profile_connection="BLOCKED_QUERY_OR_AUTHENTICATION",
                    connection_error_class=type(error).__name__,
                )
        access_now = dt.datetime.now(dt.UTC)
        report["access_checked_at"] = access_now.isoformat()
        report["robots"] = inspect_robots(
            profile.robots_directory, access_now, launch_at
        )
        report["throttle"] = throttle_check(
            rows, access_now, profile.interval, launch_at
        )
        report["throttle"]["rows_observed"] = (
            report["native_profile_connection"] == "READONLY_QUERY_SUCCEEDED"
        )
        if not report["throttle"]["rows_observed"]:
            report["throttle"].update(
                status="NOT_CHECKED_CONFIGURATION_OR_CONNECTION_BLOCKED",
                current_ready=False,
                blocked_now=None,
                backlog_seconds=None,
                blocking_reasons=["native_profile_not_inspected"],
                canonical_rows=None,
            )
        report["network_boundary"] = boundary
    report["offline_configuration_ready_now"] = (
        not profile.problems
        and report["native_profile_connection"] == "READONLY_QUERY_SUCCEEDED"
        and report["permission_check"] is not None
        and report["permission_check"]["status"] == "ACL_AND_SETTINGS_PASS_NOW_ONLY"
    )
    report["access_ready_now"] = (
        report["robots"]["current_ready"] and report["throttle"]["current_ready"]
    )
    planned_blocked = (
        launch_at is not None and report["robots"]["launch_ready"] is False
    )
    report["planned_access_state"] = (
        "NOT_CHECKED_NO_LAUNCH_TIME"
        if launch_at is None
        else "KNOWN_ROBOTS_BLOCK_AT_LAUNCH"
        if planned_blocked
        else "THROTTLE_CONTENTION_RECHECK_REQUIRED"
    )
    report["state"] = (
        "OFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING"
        if report["offline_configuration_ready_now"]
        and report["access_ready_now"]
        and not planned_blocked
        else "NO_GO"
    )
    report["throttle_rows"] = rows
    return _finish(root, report, audits, pins)


def _saved_config_metadata(path):
    if not path.is_file():
        return dict(path=str(path.relative_to(ROOT)), exists=False)
    result = dict(
        path=str(path.relative_to(ROOT)),
        exists=True,
        explicit_live_profile=False,
        container_hostname_rewritten=False,
        owner_url_used=False,
    )
    try:
        values = dotenv_values(path, interpolate=False)
        result["owner_url_present"] = bool(values.get("DATABASE_URL_OWNER"))
        raw = values.get("DATABASE_URL")
        if raw:
            url = make_url(raw)
            result["serving_endpoint"] = dict(
                driver=url.drivername,
                host=url.host,
                port=url.port,
                database=url.database,
                login=url.username,
                password_configured=bool(url.password),
            )
            result["scope"] = "compose_internal_serving_config_not_host_live_profile"
    except Exception as error:
        result["error_class"] = type(error).__name__
    return result


def inventory(output):
    root = _newroot(output)
    pins = _pins()
    now = dt.datetime.now(dt.UTC)
    with database_only() as boundary:
        data, audit = inventory_database()
        directory_raw = os.getenv("HORSERACING_ROBOTS_CACHE_DIR")
        directory = (
            ROOT / "artifacts/robots_cache"
            if directory_raw is None
            else Path(directory_raw).resolve()
            if directory_raw.strip()
            else None
        )
        raw_interval = os.getenv("HORSERACING_NETKEIBA_MIN_INTERVAL_S")
        try:
            interval = float(raw_interval) if raw_interval else 1.0
        except ValueError:
            interval = None
        access_now = dt.datetime.now(dt.UTC)
        robots = inspect_robots(directory, access_now)
        throttle = throttle_check(data["throttle_rows"], access_now, interval)
        report = dict(
            mode=MODE,
            scope="administrative_inventory_not_live_profile",
            checked_at=now.isoformat(),
            access_checked_at=access_now.isoformat(),
            state="NO_GO",
            can_collect=False,
            new_collection=False,
            database_write=False,
            live_database_url_configured=bool(os.getenv("DATABASE_URL")),
            existing_saved_config=_saved_config_metadata(ROOT / "deploy/.env"),
            database=data,
            robots=robots,
            throttle=throttle,
            network_boundary=boundary,
            source_interval_configured=raw_interval is not None,
            source_interval_operator_approval_confirmed=False,
            role_grants_applied=False,
            cache_refreshed=False,
            slot_reserved=False,
            observe_invocations=0,
            model_inference=False,
            live_plan_created=False,
            quote_max_age_seconds=1800,
            both_predictions_durable_before_decision_required=True,
            native_default_measured=False,
        )
    return _finish(root, report, [audit], pins)


def verify(output):
    import json

    root = Path(output)
    manifest = json.loads((root / "manifest.json").read_text())
    if set(manifest["files"]) | {"manifest.json"} != {
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
    }:
        raise ValueError("preflight_inventory_changed")
    for name, checksum in manifest["files"].items():
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or (root / p).is_symlink():
            raise ValueError("preflight_path_unsafe")
        if hashlib.sha256((root / p).read_bytes()).hexdigest() != checksum:
            raise ValueError("preflight_file_changed")
    pins = json.loads((root / "source-pins.json").read_text())
    for name, checksum in pins.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != checksum:
            raise ValueError("preflight_source_changed")
    result = json.loads((root / "result.json").read_text())
    if (
        result["can_collect"] is not False
        or result["new_collection"] is not False
        or result["database_write"] is not False
    ):
        raise ValueError("preflight_scope_changed")
    if (
        result.get("mode") != MODE
        or result.get("model_inference") is not False
        or result.get("live_plan_created") is not False
        or result.get("observe_invocations") != 0
        or result.get("role_grants_applied") is not False
        or result.get("cache_refreshed") is not False
        or result.get("slot_reserved") is not False
        or result.get("quote_max_age_seconds") != 1800
        or result.get("both_predictions_durable_before_decision_required") is not True
    ):
        raise ValueError("preflight_fixed_contract_changed")
    from scripts.roi_explore.db_runtime_166.database import validate_statement

    for audit in json.loads((root / "database-audits.json").read_text()):
        for row in audit["statements"]:
            validate_statement(row["sql"])
            if hashlib.sha256(row["sql"].encode()).hexdigest() != row["sha256"]:
                raise ValueError("preflight_sql_hash_changed")
    return dict(
        status="verified",
        state=result["state"],
        can_collect=False,
        new_collection=False,
        database_write=False,
    )
