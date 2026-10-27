"""Catalog/ACL inspection only. Writes, locks and collector construction are forbidden."""

from __future__ import annotations

import contextlib
import datetime as dt

from sqlalchemy import create_engine, text
from scripts.roi_explore.db_runtime_166.database import (
    attach_audit,
    create_readonly_engine,
    readonly_snapshot,
)
from .config import ROOT
import json

CONTRACT = json.loads(
    (ROOT / "specs/169-live-preflight/evidence/permission-contract.json").read_text()
)
TABLES = list(CONTRACT["select_relations"])
ROLE_SQL = """SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,
rolbypassrls,rolinherit,rolconfig FROM pg_roles ORDER BY rolname"""
IDENTITY_SQL = """SELECT current_user AS current_login,session_user AS session_login,current_database() AS database_name,
inet_server_addr()::text AS server_address,inet_server_port() AS server_port,
current_timestamp AS database_time,pg_is_in_recovery() AS standby,
current_setting('default_transaction_read_only') AS observed_default_readonly,
current_setting('transaction_read_only') AS transaction_readonly,
current_setting('transaction_isolation') AS isolation,
current_setting('search_path') AS search_path"""
SETTINGS_SQL = """SELECT d.datname,r.rolname,s.setdatabase,s.setrole,s.setconfig
FROM pg_db_role_setting s LEFT JOIN pg_database d ON d.oid=s.setdatabase
LEFT JOIN pg_roles r ON r.oid=s.setrole
WHERE s.setdatabase IN (0,(SELECT oid FROM pg_database WHERE datname=current_database()))"""
RELATION_SQL = """SELECT c.relname,c.oid::text AS relation_oid,c.relrowsecurity,c.relforcerowsecurity,
(c.relowner=(SELECT oid FROM pg_roles WHERE rolname=:role)) AS owned_by_role,
(to_regclass(c.relname)::oid=c.oid) AS unqualified_resolves_to_public,
(SELECT count(*) FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal AND t.tgenabled<>'D') AS custom_enabled_triggers,
(SELECT count(*) FROM pg_rewrite w WHERE w.ev_class=c.oid AND w.rulename<>'_RETURN') AS custom_rules,
has_table_privilege(:role,c.oid,'SELECT') AS table_select,
has_table_privilege(:role,c.oid,'DELETE') AS table_delete,
has_table_privilege(:role,c.oid,'TRUNCATE') AS table_truncate
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname=:table AND c.relkind IN ('r','p')"""
COLUMN_SQL = """SELECT a.attname,
has_column_privilege(:role,c.oid,a.attnum,'SELECT') AS can_read,
has_column_privilege(:role,c.oid,a.attnum,'INSERT') AS can_insert,
has_column_privilege(:role,c.oid,a.attnum,'UPDATE') AS can_modify
FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname=:table AND a.attnum>0 AND NOT a.attisdropped
ORDER BY a.attnum"""
BASE_SQL = """SELECT has_database_privilege(:role,current_database(),'CONNECT') AS can_connect,
has_schema_privilege(:role,'public','USAGE') AS schema_usage,
has_schema_privilege(:role,'public','CREATE') AS schema_create,
has_function_privilege(:role,'pg_catalog.clock_timestamp()','EXECUTE') AS clock_execute,
has_function_privilege(:role,'pg_catalog.txid_current_snapshot()','EXECUTE') AS snapshot_execute"""
MEMBERS_SQL = """SELECT parent.rolname AS granted_role,m.admin_option
FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid
JOIN pg_roles child ON child.oid=m.member WHERE child.rolname=:role ORDER BY parent.rolname"""
THROTTLE_SQL = """SELECT domain,next_allowed_at,blocked_until,block_reason,updated_at
FROM public.fetch_throttle_state WHERE domain=:key"""


def _encode(value):
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(v) for v in value]
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            raise ValueError("database_timestamp_naive")
        return value.astimezone(dt.UTC).isoformat()
    return value


def settings_summary(values):
    allowed = {
        "default_transaction_read_only",
        "search_path",
        "statement_timeout",
        "lock_timeout",
    }
    result = {}
    omitted = 0
    for value in values or []:
        name, _, setting = value.partition("=")
        if name in allowed:
            # Only known, non-secret system settings enter the evidence packet.
            result[name] = setting
        else:
            omitted += 1
    return dict(allowlisted_settings=result, other_settings_omitted=omitted)


def collect_catalog(conn):
    identity = _encode(dict(conn.execute(text(IDENTITY_SQL)).mappings().one()))
    roles = []
    for raw in conn.execute(text(ROLE_SQL)).mappings():
        if raw["rolname"].startswith("pg_"):
            continue
        row = dict(raw)
        config = row.pop("rolconfig")
        row["role_settings"] = settings_summary(config)
        roles.append(row)
    settings = []
    for raw in conn.execute(text(SETTINGS_SQL)).mappings():
        row = dict(raw)
        values = row.pop("setconfig")
        row["settings"] = settings_summary(values)
        settings.append(row)
    return dict(
        identity=identity, non_builtin_roles=roles, role_database_settings=settings
    )


def collect_permissions(conn, role):
    base = dict(conn.execute(text(BASE_SQL), dict(role=role)).mappings().one())
    tables = {}
    for table in TABLES:
        params = dict(role=role, table=table)
        relation = conn.execute(text(RELATION_SQL), params).mappings().one_or_none()
        columns = (
            [dict(r) for r in conn.execute(text(COLUMN_SQL), params).mappings()]
            if relation
            else []
        )
        tables[table] = dict(
            metadata=dict(relation) if relation else None, columns=columns
        )
    members = [
        dict(r) for r in conn.execute(text(MEMBERS_SQL), dict(role=role)).mappings()
    ]
    return dict(role=role, base=base, tables=tables, memberships=members)


def evaluate_permissions(role, permissions, *, identity, native_default_measured):
    reasons = []
    missing = []
    if role is None:
        return dict(
            status="BLOCKED",
            blocking_reasons=["login_role_not_found"],
            missing_required=[],
            can_collect=False,
        )
    if not (
        role.get("rolname") == permissions.get("role") == identity.get("current_login")
    ):
        reasons.append("role_identity_permission_binding_mismatch")
    if native_default_measured and identity.get("session_login") != identity.get(
        "current_login"
    ):
        reasons.append("session_login_differs_from_current_role")
    for name in (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
    ):
        if role.get(name):
            reasons.append("overprivileged_role:" + name)
    if role.get("rolcanlogin") is not True:
        reasons.append("role_cannot_login")
    for key in ("can_connect", "schema_usage", "clock_execute", "snapshot_execute"):
        if permissions["base"].get(key) is not True:
            missing.append(key)
    if permissions["base"].get("schema_create"):
        reasons.append("schema_create_not_needed_for_observer")
    if permissions["memberships"]:
        reasons.append("role_memberships_require_separate_review")
    for table, needed in CONTRACT["select_relations"].items():
        item = permissions["tables"].get(table) or {}
        metadata = item.get("metadata")
        if metadata is None:
            missing.append("relation:" + table)
            continue
        for flag in (
            "relrowsecurity",
            "relforcerowsecurity",
            "owned_by_role",
            "table_delete",
            "table_truncate",
        ):
            if metadata.get(flag):
                reasons.append(table + ":" + flag)
        if metadata.get("unqualified_resolves_to_public") is not True:
            reasons.append(table + ":search_path_resolution_unproven")
        # DML triggers on the five SELECT-only relations do not fire during observation.
        # Throttle INSERT/UPDATE triggers can change the operational write contract.
        if (
            table == "fetch_throttle_state" and metadata.get("custom_enabled_triggers")
        ) or metadata.get("custom_rules"):
            reasons.append(table + ":custom_trigger_or_rule_requires_review")
        columns = {r["attname"]: r for r in item.get("columns", [])}
        for column in needed:
            if columns.get(column, {}).get("can_read") is not True:
                missing.append(table + "." + column + ":SELECT")
        if table == "fetch_throttle_state":
            for operation, key in [("insert", "can_insert"), ("update", "can_modify")]:
                for column in CONTRACT["operational_" + operation + "_columns"]:
                    if columns.get(column, {}).get(key) is not True:
                        missing.append(table + "." + column + ":" + operation.upper())
        elif any(
            row.get("can_insert") or row.get("can_modify") for row in columns.values()
        ):
            reasons.append(table + ":write_privileges_not_needed")
    if identity.get("standby") is not False:
        reasons.append("standby_or_unknown_server_state")
    if native_default_measured:
        if identity.get("observed_default_readonly") != "off":
            reasons.append("live_native_default_disallows_operational_writes")
    else:
        reasons.append("live_native_default_not_measured_for_target_profile")
    return dict(
        status="ACL_AND_SETTINGS_PASS_NOW_ONLY"
        if not reasons and not missing
        else "BLOCKED",
        blocking_reasons=reasons,
        missing_required=missing,
        sequence_required=False,
        operational_write_probe_performed=False,
        can_collect=False,
    )


def inventory_database():
    """Explicit administrative inventory, labelled separately from the live profile."""
    engine, audit = create_readonly_engine()
    audit["phase"] = "administrative_catalog_inventory_readonly"
    try:
        with readonly_snapshot(engine, audit) as (conn, proof):
            catalog = collect_catalog(conn)
            profiles = []
            for role in catalog["non_builtin_roles"]:
                permissions = collect_permissions(conn, role["rolname"])
                profiles.append(
                    dict(
                        permissions=permissions,
                        evaluation=evaluate_permissions(
                            role,
                            permissions,
                            identity=catalog["identity"],
                            native_default_measured=False,
                        ),
                    )
                )
            throttle = _encode(
                [
                    dict(r)
                    for r in conn.execute(
                        text(THROTTLE_SQL), dict(key="netkeiba.com")
                    ).mappings()
                ]
            )
        return dict(
            scope="administrative_readonly_inventory_not_live_profile",
            catalog=catalog,
            role_permission_checks=profiles,
            throttle_rows=throttle,
            readonly_proof=proof,
            live_native_default_measured=False,
            forced_inspection_readonly=True,
        ), audit
    finally:
        engine.dispose()


@contextlib.contextmanager
def native_readonly_probe(profile):
    """Measure live native defaults without a READ WRITE transaction or write probe.

    Driver metadata initialization uses only SQLAlchemy's builtin SELECT/SHOW. The SQL gate is
    installed before connect; the first application statement makes the transaction READ ONLY.
    No startup default_readonly override is passed, unlike the administrative inventory.
    """
    if profile.problems:
        raise ValueError("invalid_live_profile")
    kwargs = dict(
        connect_timeout=5,
        application_name="manual169_preflight_only",
        options="-c statement_timeout=120000 -c lock_timeout=2000",
    )
    if profile.url.host == "localhost":
        kwargs["hostaddr"] = "127.0.0.1"
    engine = create_engine(
        profile.url,
        hide_parameters=True,
        echo=False,
        isolation_level="REPEATABLE READ",
        connect_args=kwargs,
    )
    audit = dict(
        database_write=False,
        new_collection=False,
        phase="explicit_profile_readonly_probe",
        statements=[],
        snapshots=[],
        transactions=[],
    )
    attach_audit(engine, audit)
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                conn.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                identity = dict(conn.execute(text(IDENTITY_SQL)).mappings().one())
                if (
                    identity["transaction_readonly"] != "on"
                    or identity["isolation"] != "repeatable read"
                ):
                    raise ValueError("profile_transaction_not_readonly")
                yield conn, _encode(identity), audit
            finally:
                if tx.is_active:
                    tx.rollback()
    finally:
        engine.dispose()
