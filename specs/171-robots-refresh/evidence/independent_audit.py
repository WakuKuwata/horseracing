"""Independently audit public 171 packet. No DB, HTTP, private files, or product imports."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import re


URL = "https://race.netkeiba.com/robots.txt"


def require(condition, name):
    if not condition:
        raise ValueError(name)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def at(value):
    value = dt.datetime.fromisoformat(value)
    require(value.tzinfo is not None, "naive_time")
    return value.astimezone(dt.UTC)


def verify_readonly(audit):
    require(audit["database_write"] is False, "readonly_write_claim")
    require(audit["new_collection"] is False, "readonly_collection_claim")
    require(
        bool(audit["transactions"])
        and all(t["event"] == "rollback" for t in audit["transactions"]),
        "readonly_not_rolled_back",
    )
    require(
        all(t["phase"] == audit["phase"] for t in audit["transactions"]),
        "readonly_transaction_phase",
    )
    for item in audit["statements"]:
        sql = item["sql"]
        require(
            hashlib.sha256(sql.encode()).hexdigest() == item["sha256"],
            "readonly_sql_hash",
        )
        # Privilege names occur as data literals in ACL inspection SELECTs.
        bare = re.sub(r"'(?:''|[^'])*'", "''", sql)
        bare = re.sub(r'"(?:""|[^"])*"', '""', bare)
        normalized = " ".join(bare.upper().split())
        require(
            normalized.startswith(("SELECT ", "SHOW ", "SET TRANSACTION ")),
            "readonly_sql_kind",
        )
        require(item["status"] == "completed", "readonly_statement_not_completed")
        require(
            not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|GRANT|ALTER|DROP)\b", normalized),
            "readonly_sql_mutation",
        )
    return len(audit["statements"])


def verify_preflight(folder, repo):
    manifest = read(folder / "manifest.json")["files"]
    require(
        set(manifest) | {"manifest.json"}
        == {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()},
        "preflight_file_inventory",
    )
    require(all(sha(folder / p) == h for p, h in manifest.items()), "preflight_hash")
    require(
        all(sha(repo / p) == h for p, h in read(folder / "source-pins.json").items()),
        "preflight_source_pin",
    )
    result = read(folder / "result.json")
    for field in ("can_collect", "new_collection", "database_write", "cache_refreshed", "slot_reserved", "model_inference", "live_plan_created"):
        require(result[field] is False, "preflight_scope_" + field)
    require(result["observe_invocations"] == 0, "preflight_observe")
    require(result["quote_max_age_seconds"] == 1800, "quote_contract")
    require(result["both_predictions_durable_before_decision_required"] is True, "ready_contract")
    require(result["native_identity"]["current_login"] == "hr_manual_observer", "preflight_role")
    require(result["native_identity"]["transaction_readonly"] == "on", "preflight_readonly")
    require(result["permission_check"]["status"] == "ACL_AND_SETTINGS_PASS_NOW_ONLY", "preflight_acl")
    count = sum(verify_readonly(audit) for audit in read(folder / "database-audits.json"))
    return result, count


def audit(root, final_folder):
    repo = Path(__file__).resolve().parents[3]
    scope = read(root / "request-scope.json")
    require(scope["authorized_url"] == URL and scope["maximum_http_attempts"] == 1, "authorized_scope")
    require(scope["race_data_collection"] is False, "scope_no_race")
    require(scope["global_interval_unified"] is False, "scope_interval_not_global")
    result = read(root / "refresh/result.json")
    require(result["status"] == "ROBOTS_REFRESHED", "refresh_status")
    require(result["http_requests"] == 1 and result["http_status"] == 404, "actual_http")
    for field in ("race_data_collection", "model_inference", "can_collect_race_data", "redirects_followed"):
        require(result[field] is False, "refresh_scope_" + field)
    require(result["retries"] == result["observe_invocations"] == 0, "no_retry_observe")
    require(result["throttle_state_write"] and result["slot_reserved"], "slot_write_disclosed")
    require(result["quote_max_age_seconds"] == 1800 and result["both_predictions_durable_before_decision_required"], "unchanged_contract")
    attempts = read(root / "refresh/http-attempts.json")["attempts"]
    require(len(attempts) == 1 and attempts[0]["method"] == "GET" and attempts[0]["url"] == URL, "fixed_http_attempt")
    raw = root / "refresh/robots-response.bin"
    require(raw.stat().st_size == result["response_bytes"] == 196, "response_size")
    require(sha(raw) == result["response_sha256"], "response_hash")
    cache_path = root / "refresh/robots-cache-raw.json"
    cache = read(cache_path)
    require(cache == read(root / "refresh/robots-cache-snapshot.json"), "cache_snapshot_content")
    require(sha(cache_path) == result["cache_sha256"] == result["robots"]["sha256"], "cache_original_hash")
    require(cache["schema_version"] == 1 and cache["origin"] == URL.removesuffix("/robots.txt"), "cache_identity")
    require(cache["status"] == 404 and cache["outcome"] == "absent" and base64.b64decode(cache["body"], validate=True) == b"", "cache_explicit_absence")
    fetched = at(cache["fetched_at"])
    require(at(result["started_at"]) <= fetched <= at(result["finished_at"]), "fresh_this_attempt")
    require(at(result["robots"]["expires_at"]) - fetched == dt.timedelta(days=1), "cache_ttl")
    policy = read(root / "refresh/policy-sql.json")
    require(policy["credential_values_saved"] is False, "no_policy_parameters")
    sql_items = policy["statements"]
    require(len(sql_items) == 6, "policy_statement_count")
    expected_prefixes = ("INSERT INTO FETCH_THROTTLE_STATE ", "SELECT FETCH_THROTTLE_STATE.DOMAIN,", "SELECT CLOCK_TIMESTAMP()", "UPDATE FETCH_THROTTLE_STATE SET ", "SELECT CLOCK_TIMESTAMP()", "SELECT FETCH_THROTTLE_STATE.BLOCKED_UNTIL ")
    for item, prefix in zip(sql_items, expected_prefixes, strict=True):
        require(item["parameter_values_saved"] is False, "no_sql_parameters")
        require(hashlib.sha256(item["sql"].encode()).hexdigest() == item["sha256"], "policy_sql_hash")
        require(" ".join(item["sql"].upper().split()).startswith(prefix), "policy_sql_shape")
        require(";" not in item["sql"], "policy_multi_statement")
    require("FOR UPDATE" in sql_items[1]["sql"], "policy_row_lock")
    readonly_count = verify_readonly(read(root / "refresh/target-readonly-audit.json"))
    operation_names = ("operation-before", "operation-after", "operation-final")
    operations = [read(root / name / "result.json") for name in operation_names]
    for name, operation in zip(operation_names, operations, strict=True):
        require(operation["jobs"] == [] and operation["throttle_relation_locks"] == [], "operation_current_queue_locks")
        require(len(operation["processes"]) == 3, "operation_processes")
        require(operation["future_contention_unconfirmed"] and operation["operator_policy_approval_confirmed"] is False, "operation_future_limit")
        require(operation["configuration_changed"] is operation["running_processes_restarted"] is False, "operation_no_change")
        require(operation["network_boundary"]["attempts"] == [], "operation_no_http")
        for process in operation["processes"]:
            settings = process["settings"]
            require(settings["HORSERACING_NETKEIBA_MIN_INTERVAL_S"] == dict(explicit=False, code_default_seconds=1.0), "process_default_interval")
            require(settings["shared_limiter_disabled"] is False, "process_shared_limiter")
            require(settings["DATABASE_URL"]["matches_confirmed_local_database"], "process_database")
        readonly_count += verify_readonly(read(root / name / "database-audit.json"))
    before, after, last = (op["throttle"][0] for op in operations)
    require(at(before["updated_at"]) < at(after["updated_at"]), "throttle_updated")
    require(at(after["next_allowed_at"]) - at(after["updated_at"]) == dt.timedelta(seconds=60), "throttle_interval_60")
    require(after["blocked_until"] is None, "no_cooldown")
    require(after == last, "no_other_slot_between_samples")
    immediate, count = verify_preflight(root / "preflight-immediate", repo)
    readonly_count += count
    require(immediate["state"] == "NO_GO" and immediate["throttle"]["backlog_seconds"] > 3, "immediate_own_backlog")
    final, count = verify_preflight(final_folder, repo)
    readonly_count += count
    require(final["state"] == "OFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING", "final_pending")
    require(final["access_ready_now"] and final["offline_configuration_ready_now"], "final_current_pass")
    require(final["throttle"]["backlog_seconds"] == 0 and final["throttle"]["future_contention_unconfirmed"], "final_no_future_promise")
    require(final["robots"]["sha256"] == result["cache_sha256"], "final_same_robots")
    require(final["throttle_rows"][0] == after, "no_other_slot_observed")
    require(len(final["launch_only_unconfirmed"]) >= 9, "future_gates_maintained")
    pins = read(root / "source-pins.json")["files"]
    require(all(sha(repo / p) == h for p, h in pins.items()), "initial_source_pins")
    credential_pattern = re.compile(rb"(?:postgresql(?:\+psycopg)?://[^\s\"']+:[^\s\"']+@|SCRAM-SHA-256\$\d+:[A-Za-z0-9+/=]+\$)")
    scanned = [p for p in root.rglob("*") if p.is_file()]
    require(all(credential_pattern.search(p.read_bytes()) is None for p in scanned), "public_credential_pattern")
    return dict(status="independently_verified", actual_http_requests=1, http_status=404, response_bytes=196, robots_cache_explicit_absence=True, redirect_or_retry=False, policy_sql_statements=6, throttle_mutation_only=True, race_data_collection=False, readonly_audit_statements=readonly_count, source_pins_verified=len(pins), local_processes_observed=3, current_queue_or_throttle_locks=0, manual_interval_seconds=60, observed_other_process_default_seconds=1, global_interval_unified=False, operator_policy_approval_confirmed=False, future_contention_unconfirmed=True, immediate_state=immediate["state"], final_state=final["state"], can_collect_race_data=False, quote_max_age_seconds=1800, both_predictions_durable_before_decision_required=True, public_credential_pattern_found=False, public_files_scanned=len(scanned), private_profile_content_read=False, database_requery=False, http_requery=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--final-preflight", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.root, args.final_preflight)
    with args.out.open("x") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({key: report[key] for key in ("status", "actual_http_requests", "final_state", "race_data_collection")}))
