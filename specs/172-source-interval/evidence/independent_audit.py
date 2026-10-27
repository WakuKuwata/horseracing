"""Audit public rollout evidence using stdlib only. No DB/HTTP/private-profile reads."""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]


def require(ok, code):
    if not ok:
        raise ValueError(code)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def at(value):
    value = dt.datetime.fromisoformat(value)
    require(value.tzinfo is not None, "naive_clock")
    return value.astimezone(dt.UTC)


def readonly(audit):
    require(
        audit["database_write"] is audit["new_collection"] is False, "readonly_scope"
    )
    require(
        audit["transactions"]
        and all(t["event"] == "rollback" for t in audit["transactions"]),
        "readonly_rollback",
    )
    require(
        all(s["rolled_back"] for s in audit["snapshots"]), "readonly_snapshot_rollback"
    )
    for row in audit["statements"]:
        sql = row["sql"]
        require(
            hashlib.sha256(sql.encode()).hexdigest() == row["sha256"],
            "readonly_sql_hash",
        )
        bare = re.sub(r"'(?:''|[^'])*'", "''", sql)
        bare = re.sub(r'"(?:""|[^"])*"', '""', bare)
        normalized = " ".join(bare.upper().split()).rstrip(";")
        require(
            normalized.startswith(("SELECT ", "SHOW ", "SET TRANSACTION ")),
            "readonly_statement_kind",
        )
        require(
            not re.search(
                r"\b(INSERT|UPDATE|DELETE|TRUNCATE|GRANT|ALTER|DROP|CREATE|COPY|CALL|MERGE|INTO)\b",
                normalized,
            ),
            "readonly_mutation",
        )
        require(row["status"] == "completed", "readonly_statement_status")
        require(row["phase"] == audit["phase"], "readonly_statement_phase")
    return len(audit["statements"])


def runtime(row, component):
    require(row["component"] == component, "runtime_component")
    require(
        row["source_interval_seconds"] == 60
        and set(row["effective_intervals"].values()) == {60},
        "runtime_interval",
    )
    require(
        row["collection_paused"] is row["shared_limiter_enabled"] is True,
        "runtime_pause",
    )
    for field in (
        "worker_main_imported",
        "worker_main_called",
        "recovery_called",
        "race_data_collection",
        "model_inference",
        "secret_values_saved",
    ):
        require(row[field] is False, "runtime_scope_" + field)
    require(row["external_http_requests"] == 0, "runtime_external_http")
    require(
        row["quote_max_age_seconds"] == 1800
        and row["both_predictions_durable_before_decision_required"] is True,
        "runtime_contract",
    )


def audit(run):
    policy = read(ROOT / "config/source-operations.json")
    require(
        policy
        == dict(
            schema_version="172-v1",
            source_interval_seconds=60,
            ops_fetch_min_interval_seconds=60,
            shared_limiter_enabled=True,
            collection_paused=True,
        ),
        "common_policy",
    )
    wrapper_path = ROOT / "scripts/source_operations_172.py"
    tree = ast.parse(wrapper_path.read_text())
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    require("horseracing_ops.worker" not in imports, "worker_import_present")
    wrapper = wrapper_path.read_text()
    require(
        "start_new_session=True" in wrapper
        and "subprocess.DEVNULL" in wrapper
        and "close_fds=True" in wrapper,
        "detached_session",
    )
    require(
        "class PausedOps" in wrapper and "source_collection_paused" in wrapper,
        "pause_boundaries",
    )
    # These legacy launch files contain credentials: inspect structure only, never hash/copy them.
    stack = (ROOT / "scripts/stack.sh").read_text()
    require(
        'source_operations_172.py" api' in stack
        and 'source_operations_172.py" ops-api' in stack,
        "stack_wrapper",
    )
    require("--detach" in stack and "--pidfile" in stack, "stack_detach")
    require("python -m horseracing_ops.worker" not in stack, "stack_worker_direct")
    launch = read(ROOT / ".claude/launch.json")
    api_launch = next(c for c in launch["configurations"] if c["name"] == "api")
    require(
        "source_operations_172.py" in " ".join(api_launch["runtimeArgs"]),
        "alternate_api_wrapper",
    )
    stopped = [
        read(run / name)
        for name in ("worker-stopped.json", "ops_api-stopped.json", "api-stopped.json")
    ]
    require(
        all(
            r["all_old_pids_gone"] and r["race_data_collection"] is False
            for r in stopped
        ),
        "old_processes_not_stopped",
    )
    require(
        at(stopped[0]["at"]) <= at(stopped[1]["at"]) <= at(stopped[2]["at"]),
        "stop_order",
    )
    old_pids = {pid for row in stopped for pid in row["old_pids"]}
    smoke = read(run / "detach-smoke-result.json")
    require(
        smoke["survived_exec_cell_boundary"] and smoke["stopped_after_check"],
        "detach_smoke",
    )
    require(smoke["database"] is smoke["network"] is False, "detach_smoke_scope")
    process = read(run / "runtime-processes.json")
    require(
        process["worker_daemon_started"] is process["worker_pidfile_present"] is False,
        "worker_started",
    )
    require(
        process["source_interval_seconds"] == 60 and process["collection_paused"],
        "process_policy",
    )
    services = {r["component"]: r for r in process["services"]}
    require(set(services) == {"api", "ops-api"}, "service_inventory")
    for component in services:
        report = read(run / (component + "-runtime.json"))
        runtime(report, component)
        require(
            report["outbound_python_connections_blocked"], "service_socket_boundary"
        )
        require(
            report["pid"] == services[component]["pid"]
            and report["pid"] not in old_pids,
            "new_pid",
        )
        require(
            services[component]["listener_pid_matched"]
            and services[component]["runtime_report_saved"],
            "listener_identity",
        )
        require(
            at(report["checked_at"]) > at(stopped[-1]["at"]),
            "service_started_before_stop",
        )
        require(
            report["ops_mutations_blocked_before_app"] == (component == "ops-api"),
            "ops_outer_pause",
        )
    for name in ("worker-dry-start.json", "worker-runtime.json"):
        row = read(run / name)
        runtime(row, "worker")
        require(row["worker_state"] == "PAUSED_NOT_STARTED", "worker_not_paused")
    require(
        at(read(run / "worker-runtime.json")["checked_at"]) > at(stopped[0]["at"]),
        "worker_restart_time",
    )
    manual = read(run / "manual-runtime.json")
    runtime(manual, "manual-preflight")
    require(
        manual["manual_profile_interval_seconds"] == 60
        and manual["collection_enabled"] is False,
        "manual_policy",
    )
    local = read(run / "local-http-smoke.json")
    require(
        local["all_http_requests_loopback_only"]
        and local["external_http_requests"] == 0
        and local["race_data_collection"] is False,
        "local_scope",
    )
    require(len(local["requests"]) == 5, "local_smoke_inventory")
    for row in local["requests"]:
        require(row["url"].startswith("http://127.0.0.1:"), "non_loopback_smoke")
        require(row["response_payload_saved"] is False, "http_payload_saved")
        require(
            (row["method"] == "GET" and row["status"] == 200)
            or (
                row["method"] == "POST"
                and row["status"] == 503
                and row["collection_paused_response"]
            ),
            "local_http_response",
        )
    previous = read(
        ROOT / "artifacts/171-robots-refresh/run-01/operation-final/result.json"
    )
    readonly_count = 0
    operations = []
    for name in (
        "operation-before",
        "operation-before-stop",
        "operation-after",
        "operation-final",
    ):
        row = read(run / name / "result.json")
        require(row["identity"] == previous["identity"], "database_identity")
        require(row["throttle"] == previous["throttle"], "throttle_changed")
        require(
            row["jobs"] == row["throttle_relation_locks"] == [], "competition_or_jobs"
        )
        require(
            row["proof"]["default_readonly"]
            == row["proof"]["transaction_readonly"]
            == "on",
            "operation_readonly",
        )
        require(row["network_boundary"]["attempts"] == [], "operation_network_attempt")
        readonly_count += readonly(read(run / name / "database-audit.json"))
        operations.append(row)
    final = operations[-1]
    require(len(final["processes"]) == 2, "final_process_inventory")
    require(
        {r["pid"] for r in final["processes"]} == {r["pid"] for r in services.values()},
        "final_pid_match",
    )
    for row in final["processes"]:
        require(
            row["initial_environment_observed"]
            and row["worker_argument_present"] is False,
            "initial_environment",
        )
        for key in ("HORSERACING_NETKEIBA_MIN_INTERVAL_S", "OPS_FETCH_MIN_INTERVAL"):
            require(
                row["settings"][key] == dict(explicit=True, seconds=60.0),
                "final_env_interval",
            )
        require(
            row["settings"]["shared_limiter_disabled"] is False
            and row["settings"]["shared_limiter_explicit"],
            "final_limiter",
        )
    preflight = run / "preflight"
    manifest = read(preflight / "manifest.json")["files"]
    require(
        set(manifest) | {"manifest.json"}
        == {
            p.relative_to(preflight).as_posix()
            for p in preflight.rglob("*")
            if p.is_file()
        },
        "preflight_inventory",
    )
    require(all(sha(preflight / p) == h for p, h in manifest.items()), "preflight_hash")
    require(
        all(
            sha(ROOT / p) == h for p, h in read(preflight / "source-pins.json").items()
        ),
        "preflight_source_pin",
    )
    result = read(preflight / "result.json")
    require(
        result["state"] == "OFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING"
        and result["access_ready_now"]
        and result["offline_configuration_ready_now"],
        "preflight_not_passed",
    )
    require(
        result["native_identity"]["current_login"] == "hr_manual_observer"
        and result["native_identity"]["transaction_readonly"] == "on",
        "preflight_identity",
    )
    require(
        result["permission_check"]["status"] == "ACL_AND_SETTINGS_PASS_NOW_ONLY",
        "preflight_acl",
    )
    for key in (
        "can_collect",
        "new_collection",
        "database_write",
        "cache_refreshed",
        "slot_reserved",
        "model_inference",
        "live_plan_created",
    ):
        require(result[key] is False, "preflight_scope_" + key)
    prior_robots = read(
        ROOT / "artifacts/171-robots-refresh/run-01/refresh/result.json"
    )["robots"]
    require(
        result["robots"]["sha256"] == prior_robots["sha256"]
        and result["robots"]["fetched_at"] == prior_robots["fetched_at"],
        "robots_refreshed",
    )
    require(
        result["throttle_rows"] == previous["throttle"], "preflight_throttle_changed"
    )
    require(
        result["throttle"]["future_contention_unconfirmed"] is True,
        "future_contention_claim",
    )
    require(
        result["quote_max_age_seconds"] == 1800
        and result["both_predictions_durable_before_decision_required"] is True
        and result["observe_invocations"] == 0,
        "preflight_contract",
    )
    readonly_count += sum(readonly(a) for a in read(preflight / "database-audits.json"))
    credential_pattern = re.compile(
        rb"(?:postgresql(?:\+psycopg)?://[^\s\"']+:[^\s\"']+@|SCRAM-SHA-256\$\d+:[A-Za-z0-9+/=]+\$)"
    )
    public = [p for p in run.rglob("*") if p.is_file()]
    require(
        all(credential_pattern.search(p.read_bytes()) is None for p in public),
        "public_credential",
    )
    return dict(
        status="independently_verified",
        interval_seconds=60,
        services_configured=2,
        manual_profile_effective_interval=60,
        worker_stopped_and_restart_paused=True,
        old_pids_gone=True,
        listener_and_runtime_pids_matched=True,
        ops_post_denied_before_enqueue=True,
        loopback_http_checks=5,
        readonly_statements_verified=readonly_count,
        current_queue_and_throttle_locks=0,
        throttle_unchanged_since_171=True,
        robots_not_refreshed=True,
        preflight_state=result["state"],
        can_collect=False,
        quote_max_age_seconds=1800,
        both_predictions_durable_before_decision_required=True,
        credential_pattern_found=False,
        private_profile_content_read=False,
        database_requery=False,
        http_requery=False,
        safe_source_hashes={
            p: sha(ROOT / p)
            for p in (
                "config/source-operations.json",
                "scripts/source_operations_172.py",
            )
        },
        credential_bearing_launch_files_hashed=False,
    )


def audit_final(run):
    result = audit(run)
    prior_audit = read(Path(__file__).parent / "independent-audit.json")
    require(
        all(sha(ROOT / p) == h for p, h in prior_audit["safe_source_hashes"].items()),
        "running_api_ops_wrapper_changed_after_start",
    )
    manual_path = ROOT / "scripts/manual_observer_172.py"
    tree = ast.parse(manual_path.read_text())
    modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    require(
        not any(m and "manual_observation_165" in m for m in modules),
        "formal_manual_imports_old_observer",
    )
    manual = read(run / "manual-observe-paused.json")
    runtime(manual, "manual-observe")
    require(manual["manual_profile_interval_seconds"] == 60, "manual_observe_interval")
    require(manual["state"] == "PAUSED_NOT_STARTED", "manual_observe_not_paused")
    for field in (
        "collection_enabled",
        "manual_observer_imported",
        "manual_observer_called",
        "database_access",
        "source_slot_reserved",
        "official_settlement_invoked",
        "can_collect",
    ):
        require(manual[field] is False, "manual_observe_scope_" + field)
    invocation = read(run / "manual-observe-invocation.json")
    require(
        invocation["exit_code"] == 3 and invocation["state"] == "PAUSED_NOT_STARTED",
        "manual_observe_exit",
    )
    require(
        invocation["external_http_requests"] == invocation["observe_invocations"] == 0,
        "manual_observe_invocation",
    )
    require(
        invocation["database_access"] is invocation["can_collect"] is False,
        "manual_observe_invocation_scope",
    )
    preflight_runtime = read(run / "manual-preflight-runtime.json")
    runtime(preflight_runtime, "manual-preflight")
    require(
        preflight_runtime["manual_profile_interval_seconds"] == 60
        and preflight_runtime["collection_enabled"] is False,
        "manual_preflight_interval",
    )
    folder = run / "preflight-manual"
    manifest = read(folder / "manifest.json")["files"]
    require(
        set(manifest) | {"manifest.json"}
        == {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()},
        "manual_preflight_inventory",
    )
    require(
        all(sha(folder / p) == h for p, h in manifest.items()), "manual_preflight_hash"
    )
    require(
        all(sha(ROOT / p) == h for p, h in read(folder / "source-pins.json").items()),
        "manual_preflight_source_pin",
    )
    preflight = read(folder / "result.json")
    require(
        preflight["state"] == "OFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING"
        and preflight["access_ready_now"]
        and preflight["offline_configuration_ready_now"],
        "manual_preflight_not_passed",
    )
    for field in (
        "can_collect",
        "new_collection",
        "database_write",
        "cache_refreshed",
        "slot_reserved",
        "model_inference",
        "live_plan_created",
    ):
        require(preflight[field] is False, "manual_preflight_scope_" + field)
    require(
        preflight["native_identity"]["current_login"] == "hr_manual_observer"
        and preflight["permission_check"]["status"] == "ACL_AND_SETTINGS_PASS_NOW_ONLY",
        "manual_preflight_role_acl",
    )
    require(
        preflight["quote_max_age_seconds"] == 1800
        and preflight["both_predictions_durable_before_decision_required"] is True
        and preflight["observe_invocations"] == 0,
        "manual_contract",
    )
    previous = read(run / "preflight/result.json")
    require(
        preflight["robots"]["sha256"] == previous["robots"]["sha256"]
        and preflight["robots"]["fetched_at"] == previous["robots"]["fetched_at"],
        "manual_robots_refreshed",
    )
    require(
        preflight["throttle_rows"] == previous["throttle_rows"],
        "manual_throttle_changed",
    )
    require(
        preflight["launch_only_unconfirmed"] == previous["launch_only_unconfirmed"],
        "manual_future_gates",
    )
    count = sum(readonly(a) for a in read(folder / "database-audits.json"))
    last = read(run / "operation-post-manual/result.json")
    former = read(run / "operation-final/result.json")
    require(
        last["identity"] == former["identity"]
        and last["throttle"] == former["throttle"],
        "post_manual_database_or_throttle_changed",
    )
    require(
        last["jobs"] == last["throttle_relation_locks"] == [], "post_manual_job_or_lock"
    )
    require(last["processes"] == former["processes"], "api_ops_process_config_changed")
    require(last["network_boundary"]["attempts"] == [], "post_manual_network")
    count += readonly(read(run / "operation-post-manual/database-audit.json"))
    require(
        read(run / "worker-restart-paused.json") == read(run / "worker-runtime.json"),
        "worker_paused_copy",
    )
    test_text = (Path(__file__).parent / "tests-final.txt").read_text()
    require("282 passed" in test_text, "final_test_count")
    result.update(
        readonly_statements_verified=result["readonly_statements_verified"] + count,
        formal_manual_entrypoint_verified=True,
        manual_observe_paused_before_old_observer_import=True,
        manual_observe_exit_code=3,
        manual_observe_database_or_http=False,
        formal_manual_preflight_state=preflight["state"],
        api_ops_source_unchanged_since_rollout=True,
        post_manual_queue_and_throttle_unchanged=True,
        tests_passed=282,
    )
    result["safe_source_hashes"]["scripts/manual_observer_172.py"] = sha(manual_path)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = audit_final(args.root)
    with args.out.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "interval_seconds",
                    "worker_stopped_and_restart_paused",
                    "preflight_state",
                )
            }
        )
    )
