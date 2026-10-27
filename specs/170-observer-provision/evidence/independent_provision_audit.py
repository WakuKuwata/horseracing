"""Direct public packet/ACL audit; no DB, provisioning imports or private-profile reads."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import re
import stat
from pathlib import Path
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[3]
ROLE = "hr_manual_observer"
ORIGIN = "https://race.netkeiba.com"
UA = "horseracing-live/0.1 (personal use; contact via repo)"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def at(value):
    result = dt.datetime.fromisoformat(value)
    assert result.tzinfo is not None
    return result.astimezone(dt.UTC)


def readonly_sql(sql):
    bare = re.sub(r"'(?:''|[^'])*'", "''", sql)
    bare = re.sub(r'"(?:""|[^"])*"', '""', bare)
    normalized = " ".join(bare.upper().split()).rstrip(";")
    assert "--" not in bare and "/*" not in bare and ";" not in normalized
    if normalized.startswith(("SELECT ", "SHOW ")):
        assert not re.search(
            r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|COPY|CALL|GRANT|REVOKE|INTO|SET_CONFIG)\b",
            normalized,
        )
        assert "FOR UPDATE" not in normalized
    else:
        assert normalized in (
            "SET TRANSACTION READ ONLY",
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )


def target_audit(root):
    target = read(root / "target-readonly.json")
    assert target["endpoint"] == dict(
        host="127.0.0.1", port=15432, database="horseracing"
    )
    identity = target["identity"]
    assert (
        identity["database_name"] == "horseracing"
        and identity["database_oid"].isdigit()
    )
    assert identity["system_identifier"].isdigit() and identity["standby"] is False
    assert target["observer_role_already_exists"] is False
    assert (
        target["proof"]["default_readonly"]
        == target["proof"]["transaction_readonly"]
        == "on"
    )
    assert target["proof"]["isolation"] == "repeatable read"
    rows = {r["race_id"]: r for r in target["known_saved_races"]}
    assert rows["202605040907"]["horses"] == rows["202605040907"]["results"] == 15
    assert rows["202609040611"]["horses"] == rows["202609040611"]["results"] == 16
    assert target["network_boundary"]["attempts"] == []
    runtime = read(root / "runtime-endpoints.json")
    assert runtime["secret_values_saved"] is False
    matches = [
        r
        for r in runtime["processes"]
        if r.get("database_endpoint", {}).get("port") == 15432
    ]
    assert any(r["service"] == "api" for r in matches)
    assert any(r["service"] == "ops" for r in matches)
    assert all(r["database_endpoint"]["database"] == "horseracing" for r in matches)
    return dict(
        system_identifier=identity["system_identifier"],
        database_oid=identity["database_oid"],
        configured_api_and_ops_endpoint_matched=True,
    )


def provision_audit(root):
    receipt = read(root / "provision-receipt.json")
    expected = read(root / "target-readonly.json")["identity"]
    assert (
        receipt["role"] == ROLE
        and receipt["phase"] == "PROVISIONED_AND_PROFILE_PUBLISHED"
    )
    assert all(
        receipt[key] is True
        for key in (
            "role_created",
            "db_transaction_committed",
            "profile_published",
            "role_catalog_changed",
            "column_acl_changed",
        )
    )
    assert all(
        receipt[key] is False
        for key in (
            "new_collection",
            "race_data_write",
            "throttle_state_write",
            "slot_reserved",
            "cache_refreshed",
            "model_inference",
            "secret_material_logged",
            "secret_or_verifier_hash_saved",
        )
    )
    assert (
        receipt["observe_invocations"] == 0 and receipt["quote_max_age_seconds"] == 1800
    )
    assert receipt["both_predictions_durable_before_decision_required"] is True
    assert (
        receipt["source_interval_seconds"] >= 60
        and receipt["source_interval_launch_confirmation_required"] is True
    )
    for key in (
        "database_name",
        "database_oid",
        "system_identifier",
        "server_address",
        "server_port",
    ):
        assert receipt["identity"][key] == expected[key]
    assert receipt["identity"]["standby"] is False
    assert receipt["throttle_before_sha256"] == receipt["throttle_after_sha256"]
    logging = receipt["logging_protection"]
    expected_logging = dict(
        log_statement="none",
        log_min_duration_statement="-1",
        log_min_duration_sample="-1",
        log_statement_sample_rate="0",
        log_transaction_sample_rate="0",
        log_duration="off",
        log_min_error_statement="panic",
        log_min_messages="panic",
        log_parameter_max_length="0",
        log_parameter_max_length_on_error="0",
    )
    assert logging["session_logging"] == expected_logging
    assert (
        logging["preload_libraries_empty"] is True
        and logging["enabled_event_triggers"] == 0
    )
    assert logging["extensions"] == ["plpgsql"]
    assert (
        logging["cleartext_password_sent_as_sql"]
        is logging["verifier_in_public_artifact"]
        is False
    )
    contract = read(ROOT / "specs/169-live-preflight/evidence/permission-contract.json")
    assert receipt["grants"]["select_columns"] == contract["select_relations"]
    assert set(receipt["grants"]["insert_columns"]) == set(
        contract["operational_insert_columns"]
    )
    assert set(receipt["grants"]["update_columns"]) == set(
        contract["operational_update_columns"]
    )
    storage = receipt["credential_storage"]
    private = Path(storage["profile_path"])
    assert int(storage["mode"], 8) == 0o600
    st = private.lstat()
    assert (
        stat.S_ISREG(st.st_mode)
        and stat.S_IMODE(st.st_mode) == 0o600
        and st.st_nlink == 1
    )
    assert receipt["network_boundary"]["attempts"] == []
    events = [read(p) for p in sorted((root / "events").glob("*.json"))]
    assert len(events) == 4
    assert all(
        at(before["at"]) <= at(after["at"]) for before, after in zip(events, events[1:])
    )
    assert all(
        e.get("secret_material_saved_publicly") is False
        for e in (events[0], events[2], events[3])
    )
    assert (
        events[1]["role_absent"] is True
        and events[2]["db_transaction_committed"] is True
    )
    assert events[3]["profile_path"] == str(private)
    for p in root.rglob("*.json"):
        public = p.read_text()
        assert re.search(r"SCRAM-SHA-256\$[0-9]+:", public) is None
        assert "postgresql://" not in public and "postgresql+psycopg://" not in public
    return dict(
        role=ROLE,
        role_catalog_and_acl_changes_applied=True,
        race_data_changed=False,
        throttle_state_digest_unchanged=True,
        private_profile_mode="0600",
        private_profile_content_read=False,
        logging_and_atomic_publication_receipts_verified=True,
    )


def cache_audit(result):
    cache = result["robots"]
    assert (
        cache["can_collect"]
        is cache["cache_refreshed"]
        is cache["slot_reserved"]
        is False
    )
    assert cache["launch_recheck_required"] is True
    if not cache.get("sha256"):
        return
    path = Path(cache["path"])
    assert sha(path) == cache["sha256"]
    doc = read(path)
    assert doc["schema_version"] == 1 and doc["origin"] == ORIGIN
    body = base64.b64decode(doc["body"], validate=True)
    assert len(body) <= 512 * 1024
    if doc["outcome"] == "rules":
        assert doc["status"] == 200
        parser = RobotFileParser()
        parser.parse(body.decode("utf-8", errors="replace").splitlines())
        allowed = parser.can_fetch(UA, ORIGIN + "/api/api_get_jra_odds.html")
    else:
        assert (
            doc["outcome"] == "absent" and doc["status"] in (404, 410) and body == b""
        )
        allowed = True
    age = (at(cache["checked_at"]) - at(doc["fetched_at"])).total_seconds()
    fresh = 0 <= age < 86400
    assert cache["current_fresh"] == fresh and cache["allowed"] == allowed
    assert cache["current_ready"] == (fresh and allowed)


def read_smoke_audit(root):
    result = read(root / "observer-read-smoke.json")
    contract = read(ROOT / "specs/169-live-preflight/evidence/permission-contract.json")
    assert result["status"] == "passed"
    assert all(
        result[key] is False
        for key in ("business_write_probe", "new_collection", "source_slot_reserved")
    )
    identity = result["identity"]
    assert identity["current_login"] == identity["session_login"] == ROLE
    assert identity["observed_default_readonly"] == "off"
    assert identity["transaction_readonly"] == "on"
    assert identity["isolation"] == "repeatable read"
    assert result["count_star_with_column_select"] == 15
    projections = {row["table"]: row for row in result["projection_checks"]}
    assert set(projections) == set(contract["select_relations"])
    for name, columns in contract["select_relations"].items():
        assert projections[name]["status"] == "passed"
        assert projections[name]["selected_columns"] == columns
    relations = result["public_relation_acls"]
    assert len({row["relname"] for row in relations}) == len(relations)
    assert set(contract["select_relations"]) <= {row["relname"] for row in relations}
    for row in relations:
        assert row["any_select"] == (row["relname"] in projections)
        assert row["any_insert"] == (row["relname"] == "fetch_throttle_state")
        assert row["any_update"] == (row["relname"] == "fetch_throttle_state")
        assert row["can_delete"] is row["can_truncate"] is False
    assert all(
        row[key] is False
        for row in result["sequence_acls"]
        for key in ("usage", "select_sequence", "update_sequence")
    )
    assert result["network_boundary"]["attempts"] == []
    audit = read(root / "observer-read-smoke-audit.json")
    assert audit["database_write"] is audit["new_collection"] is False
    for statement in audit["statements"]:
        readonly_sql(statement["sql"])
        assert statement["status"] == "completed" and statement["sqlstate"] is None
        assert sha_sql(statement["sql"]) == statement["sha256"]
    assert audit["transactions"]
    assert all(tx["event"] == "rollback" for tx in audit["transactions"])
    return dict(
        projection_checks=len(projections),
        count_star_with_column_select=15,
        public_relations_checked=len(relations),
        extra_relation_or_sequence_privileges=False,
        readonly_queries=len(audit["statements"]),
    )


def sha_sql(sql):
    return hashlib.sha256(sql.encode()).hexdigest()


def preflight_audit(root):
    manifest = read(root / "manifest.json")
    assert (
        manifest["mode"] == "live_preflight_only" and manifest["can_collect"] is False
    )
    assert set(manifest["files"]) | {"manifest.json"} == {
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
    }
    for name, value in manifest["files"].items():
        relative = Path(name)
        assert (
            not relative.is_absolute()
            and ".." not in relative.parts
            and not (root / relative).is_symlink()
        )
        assert sha(root / relative) == value
    for name, value in read(root / "source-pins.json").items():
        assert sha(ROOT / name) == value
    result = read(root / "result.json")
    assert result["scope"] == "explicit_live_profile_only"
    for key in (
        "can_collect",
        "new_collection",
        "database_write",
        "live_plan_created",
        "model_inference",
        "role_grants_applied",
        "cache_refreshed",
        "slot_reserved",
    ):
        assert result[key] is False, key
    assert result["observe_invocations"] == 0
    assert (
        result["quote_max_age_seconds"] == 1800
        and result["both_predictions_durable_before_decision_required"] is True
    )
    assert result["configuration_problems"] == []
    assert result["native_profile_connection"] == "READONLY_QUERY_SUCCEEDED"
    assert result["native_default_measured"] is True
    assert result["offline_configuration_ready_now"] is True
    assert result["permission_check"]["status"] == "ACL_AND_SETTINGS_PASS_NOW_ONLY"
    identity = result["native_identity"]
    assert identity["current_login"] == identity["session_login"] == ROLE
    assert identity["database_name"] == "horseracing" and identity["standby"] is False
    assert identity["observed_default_readonly"] == "off"
    assert (
        identity["transaction_readonly"] == "on"
        and identity["isolation"] == "repeatable read"
    )
    role = next(
        r for r in result["catalog"]["non_builtin_roles"] if r["rolname"] == ROLE
    )
    assert role["rolcanlogin"] is True
    assert all(
        role[k] is False
        for k in (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolreplication",
            "rolbypassrls",
            "rolinherit",
        )
    )
    permissions = result["permissions"]
    assert permissions["role"] == ROLE and permissions["memberships"] == []
    assert all(
        permissions["base"][k] is True
        for k in ("can_connect", "schema_usage", "clock_execute", "snapshot_execute")
    )
    assert permissions["base"]["schema_create"] is False
    contract = read(ROOT / "specs/169-live-preflight/evidence/permission-contract.json")
    for table, required in contract["select_relations"].items():
        item = permissions["tables"][table]
        meta = item["metadata"]
        assert meta["unqualified_resolves_to_public"] is True
        assert all(
            meta[k] is False
            for k in (
                "relrowsecurity",
                "relforcerowsecurity",
                "owned_by_role",
                "table_delete",
                "table_truncate",
            )
        )
        assert meta["custom_rules"] == 0
        cols = {r["attname"]: r for r in item["columns"]}
        assert {name for name, row in cols.items() if row["can_read"]} == set(required)
        if table == "fetch_throttle_state":
            assert meta["custom_enabled_triggers"] == 0
            for op, key in [("insert", "can_insert"), ("update", "can_modify")]:
                assert {name for name, row in cols.items() if row[key]} == set(
                    contract["operational_" + op + "_columns"]
                )
        else:
            assert all(
                row["can_insert"] is False and row["can_modify"] is False
                for row in cols.values()
            )
    count = 0
    for audit in read(root / "database-audits.json"):
        assert audit["database_write"] is audit["new_collection"] is False
        for statement in audit["statements"]:
            readonly_sql(statement["sql"])
            assert (
                hashlib.sha256(statement["sql"].encode()).hexdigest()
                == statement["sha256"]
            )
            assert statement["status"] == "completed"
            count += 1
        assert all(tx["event"] == "rollback" for tx in audit["transactions"])
    assert result["network_boundary"]["attempts"] == []
    profile = Path(result["profile"]["profile_path"])
    st = profile.lstat()
    assert (
        stat.S_ISREG(st.st_mode)
        and stat.S_IMODE(st.st_mode) == 0o600
        and st.st_nlink == 1
    )
    assert (
        result["profile"]["fallback_used"]
        is result["profile"]["owner_url_used"]
        is False
    )
    assert result["profile"]["endpoint"]["login"] == ROLE
    # Do not read the private file. Inspect only public packet text for credential forms.
    for p in root.rglob("*.json"):
        text = p.read_text()
        assert "postgresql://" not in text and "postgresql+psycopg://" not in text
        assert re.search(r"SCRAM-SHA-256\$[0-9]+:", text) is None
    cache_audit(result)
    throttle = result["throttle"]
    assert (
        throttle["can_collect"]
        is throttle["slot_reserved"]
        is throttle["cooldown_cleared"]
        is False
    )
    assert (
        throttle["launch_recheck_required"] is True
        and throttle["future_contention_unconfirmed"] is True
    )
    assert throttle["launch_ready"] is None
    return dict(
        path=str(root.relative_to(ROOT)),
        manifest_sha256=sha(root / "manifest.json"),
        native_role=ROLE,
        role_acl_and_native_default_verified=True,
        readonly_preflight_queries=count,
        private_profile_content_read=False,
        private_profile_mode="0600",
        state=result["state"],
        can_collect=False,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = dict(
        status="independently_verified",
        target=target_audit(args.root.resolve()),
        provision=provision_audit(args.root.resolve()),
        read_smoke=read_smoke_audit(args.root.resolve()),
        preflight=preflight_audit(args.preflight.resolve()),
        database_requery_performed=False,
        private_profile_content_read=False,
        new_collection=False,
    )
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
