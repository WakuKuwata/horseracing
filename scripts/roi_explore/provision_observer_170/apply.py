"""Authorized, target-bound observer provisioning. Never fetches or reserves source state.

Password and SCRAM verifier live only in memory/private profile/database auth catalog.
The administrative connection disables statement/error/duration/sampling logs before BEGIN.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import secrets
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from scripts.roi_explore.db_runtime_166.database import LOCAL_URL, database_only
from scripts.roi_explore.prospective_rehearsal_162.store import publish, read
from .private_profile import stage_profile, publish_profile

ROOT = Path(__file__).resolve().parents[3]
ROLE = "hr_manual_observer"
DB = "horseracing"
ENDPOINT = dict(host="127.0.0.1", port=15432, database=DB)
LOG_SETTINGS = {
    "log_statement": "none",
    "log_min_duration_statement": "-1",
    "log_min_duration_sample": "-1",
    "log_statement_sample_rate": "0",
    "log_transaction_sample_rate": "0",
    "log_duration": "off",
    "log_min_error_statement": "panic",
    "log_min_messages": "panic",
    "log_parameter_max_length": "0",
    "log_parameter_max_length_on_error": "0",
}
IDENTITY = """SELECT current_database(),(SELECT oid::text FROM pg_database WHERE datname=current_database()),
(pg_control_system()).system_identifier::text,inet_server_addr()::text,inet_server_port(),pg_is_in_recovery()"""


class ProvisionStopped(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise ProvisionStopped(code)


def _event(root, number, name, value):
    publish(
        root / "events" / f"{number:02}-{name}.json",
        dict(at=dt.datetime.now(dt.UTC).isoformat(), **value),
    )


def _contract():
    name = "specs/169-live-preflight/evidence/permission-contract.json"
    expected = read(ROOT / "specs/169-live-preflight/evidence/delivery-receipt.json")[
        "files"
    ][name]
    require(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected,
        "permission_contract_changed",
    )
    return read(ROOT / name)


def profile_body(password, interval):
    # Token URL-safe password requires no shell interpretation and contains no URL delimiters.
    from sqlalchemy.engine import URL

    url = URL.create(
        "postgresql+psycopg",
        username=ROLE,
        password=password,
        host=ENDPOINT["host"],
        port=ENDPOINT["port"],
        database=DB,
    )
    return (
        "# Private observer profile. Never shell-source or print this file.\n"
        "HORSERACING_OBSERVER_PROFILE_VERSION=169-v1\n"
        "HORSERACING_ENDPOINT_CONFIRMED=true\n"
        f"HORSERACING_EXPECTED_DATABASE={DB}\nHORSERACING_EXPECTED_ROLE={ROLE}\n"
        f"DATABASE_URL={url.render_as_string(hide_password=False)}\n"
        f"HORSERACING_NETKEIBA_MIN_INTERVAL_S={interval:g}\n"
    ).encode()


def _identity(row):
    return dict(
        zip(
            (
                "database_name",
                "database_oid",
                "system_identifier",
                "server_address",
                "server_port",
                "standby",
            ),
            row,
            strict=True,
        )
    )


def match_identity(actual, expected):
    return (
        all(
            actual[k] == expected[k]
            for k in (
                "database_name",
                "database_oid",
                "system_identifier",
                "server_address",
                "server_port",
            )
        )
        and actual["standby"] is False
    )


def _throttle_digest(conn):
    rows = conn.execute(
        "SELECT domain,next_allowed_at,blocked_until,block_reason,updated_at FROM public.fetch_throttle_state ORDER BY domain"
    ).fetchall()
    body = json.dumps(
        rows, default=lambda v: v.isoformat(), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(body).hexdigest()


def _log_guard(conn):
    settings = dict(
        conn.execute(
            "SELECT name,setting FROM pg_settings WHERE name = ANY(%s)",
            (list(LOG_SETTINGS),),
        ).fetchall()
    )
    require(settings == LOG_SETTINGS, "session_logging_not_suppressed")
    extras = dict(
        conn.execute(
            "SELECT name,setting FROM pg_settings WHERE name IN ('shared_preload_libraries','session_preload_libraries','local_preload_libraries')"
        ).fetchall()
    )
    require(all(value == "" for value in extras.values()), "unreviewed_preload_library")
    require(
        conn.execute(
            "SELECT count(*) FROM pg_event_trigger WHERE evtenabled<>%s", ("D",)
        ).fetchone()[0]
        == 0,
        "unreviewed_event_trigger",
    )
    extensions = [row[0] for row in conn.execute("SELECT extname FROM pg_extension")]
    require(set(extensions) == {"plpgsql"}, "unreviewed_extension_logging")
    return dict(
        session_logging=settings,
        preload_libraries_empty=True,
        enabled_event_triggers=0,
        extensions=extensions,
        cleartext_password_sent_as_sql=False,
        verifier_in_public_artifact=False,
    )


def grant_contract(conn, contract):
    role = sql.Identifier(ROLE)
    conn.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(DB), role)
    )
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    for table, columns in contract["select_relations"].items():
        conn.execute(
            sql.SQL("GRANT SELECT ({}) ON TABLE {} TO {}").format(
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.Identifier("public", table),
                role,
            )
        )
    for action, key in [
        ("INSERT", "operational_insert_columns"),
        ("UPDATE", "operational_update_columns"),
    ]:
        conn.execute(
            sql.SQL("GRANT " + action + " ({}) ON TABLE {} TO {}").format(
                sql.SQL(", ").join(map(sql.Identifier, contract[key])),
                sql.Identifier("public", "fetch_throttle_state"),
                role,
            )
        )
    conn.execute(
        sql.SQL(
            "ALTER ROLE {} IN DATABASE {} SET default_transaction_read_only = off"
        ).format(role, sql.Identifier(DB))
    )
    explicit = []
    for function in contract["builtins_execute"]:
        ok = conn.execute(
            "SELECT has_function_privilege(%s,%s,%s)", (ROLE, function, "EXECUTE")
        ).fetchone()[0]
        if not ok:
            # Only the two pinned builtin identities may be granted, never arbitrary SQL.
            require(
                function
                in (
                    "pg_catalog.clock_timestamp()",
                    "pg_catalog.txid_current_snapshot()",
                ),
                "unexpected_builtin",
            )
            conn.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION " + function + " TO {}").format(role)
            )
            explicit.append(function)
    return explicit


def apply(root_path, *, interval=60):
    root = Path(root_path).resolve()
    require(interval >= 60, "interval_would_weaken_conservative_profile")
    require(
        not (root / "provision-receipt.json").exists(),
        "provision_receipt_already_exists",
    )
    require((root / "target-readonly.json").is_file(), "target_confirmation_missing")
    contract = _contract()
    target = read(root / "target-readonly.json")
    require(
        target["endpoint"] == ENDPOINT
        and target["observer_role_already_exists"] is False,
        "target_not_confirmed_or_role_preexisting",
    )
    saved = read(root / "runtime-endpoints.json")
    actual = [
        r.get("database_endpoint")
        for r in saved["processes"]
        if isinstance(r.get("database_endpoint"), dict)
    ]
    require(
        any(
            r["database"] == DB
            and r["port"] == 15432
            and r["host"] in ("localhost", "127.0.0.1")
            for r in actual
        ),
        "application_endpoint_not_confirmed",
    )
    current = root.parent.parent.parent / "config/.env.manual-observer"
    require(
        current == ROOT / "config/.env.manual-observer", "unexpected_profile_location"
    )
    previous = current.read_bytes()
    require(
        b"REPLACE_WITH_PRIVATE_SECRET" in previous
        and b"HORSERACING_ENDPOINT_CONFIRMED=false" in previous,
        "existing_profile_not_placeholder",
    )
    staged = ROOT / "config/.env.manual-observer.staged170"
    lock_path = ROOT / "config/.env.manual-observer.provision170.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    report = dict(
        role=ROLE,
        identity=target["identity"],
        endpoint=ENDPOINT,
        phase="INITIALIZED",
        role_created=False,
        db_transaction_committed=False,
        profile_published=False,
        new_collection=False,
        race_data_write=False,
        throttle_state_write=False,
        slot_reserved=False,
        cache_refreshed=False,
        observe_invocations=0,
        model_inference=False,
        secret_material_logged=False,
        secret_or_verifier_hash_saved=False,
        role_catalog_changed=False,
        column_acl_changed=False,
        quote_max_age_seconds=1800,
        both_predictions_durable_before_decision_required=True,
        credential_storage=dict(path=str(current), mode="0600"),
        source_interval_seconds=interval,
        source_interval_launch_confirmation_required=True,
    )
    phase = "lock"
    commit_attempted = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        password = secrets.token_urlsafe(48)
        phase = "private_staging"
        stage_profile(staged, profile_body(password, interval))
        report["phase"] = "SECRET_DURABLY_STAGED"
        _event(
            root,
            1,
            "secret-staged",
            dict(
                profile_path=str(staged),
                mode="0600",
                secret_material_saved_publicly=False,
            ),
        )
        admin = make_url(LOCAL_URL)
        options = " ".join(
            "-c " + key + "=" + value for key, value in LOG_SETTINGS.items()
        )
        phase = "admin_connection"
        with database_only() as network:
            with psycopg.connect(
                host=ENDPOINT["host"],
                port=ENDPOINT["port"],
                dbname=DB,
                user=admin.username,
                password=admin.password,
                connect_timeout=5,
                application_name="manual170_authorized_role_provision",
                options=options + " -c statement_timeout=30000 -c lock_timeout=3000",
                autocommit=True,
            ) as conn:
                report["logging_protection"] = _log_guard(conn)
                with conn.transaction():
                    phase = "target_guard"
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        ("observer170:" + ROLE,),
                    )
                    ident = _identity(conn.execute(IDENTITY).fetchone())
                    require(
                        match_identity(ident, target["identity"]),
                        "target_database_identity_changed",
                    )
                    require(
                        conn.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s)",
                            (ROLE,),
                        ).fetchone()[0]
                        is False,
                        "role_already_exists_no_rotation",
                    )
                    _event(
                        root,
                        2,
                        "target-reconfirmed",
                        dict(
                            identity=ident, role_absent=True, source_slot_reserved=False
                        ),
                    )
                    report["throttle_before_sha256"] = _throttle_digest(conn)
                    phase = "create_role"
                    verifier = conn.pgconn.encrypt_password(
                        password.encode(), ROLE.encode(), b"scram-sha-256"
                    ).decode("ascii")
                    require(
                        verifier.startswith("SCRAM-SHA-256$"),
                        "unexpected_password_verifier",
                    )
                    conn.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
                        ).format(sql.Identifier(ROLE), sql.Literal(verifier))
                    )
                    phase = "grant_minimal_columns"
                    report["explicit_builtin_execute_grants"] = grant_contract(
                        conn, contract
                    )
                    require(
                        _throttle_digest(conn) == report["throttle_before_sha256"],
                        "throttle_changed_during_provision",
                    )
                    phase = "commit"
                    commit_attempted = True
                report.update(
                    db_transaction_committed=True,
                    role_created=True,
                    role_catalog_changed=True,
                    column_acl_changed=True,
                    phase="ROLE_AND_ACL_COMMITTED",
                    grants=dict(
                        select_columns=contract["select_relations"],
                        insert_columns=contract["operational_insert_columns"],
                        update_columns=contract["operational_update_columns"],
                    ),
                )
                report["throttle_after_sha256"] = _throttle_digest(conn)
                require(
                    report["throttle_after_sha256"] == report["throttle_before_sha256"],
                    "throttle_changed_after_commit",
                )
            report["network_boundary"] = network
        _event(
            root,
            3,
            "role-committed",
            dict(
                role=ROLE,
                db_transaction_committed=True,
                secret_material_saved_publicly=False,
            ),
        )
        phase = "publish_profile"
        report["credential_storage"] = publish_profile(
            staged, current, expected_previous=previous
        )
        report.update(profile_published=True, phase="PROVISIONED_AND_PROFILE_PUBLISHED")
        _event(
            root,
            4,
            "profile-published",
            dict(
                profile_path=str(current),
                mode="0600",
                secret_material_saved_publicly=False,
            ),
        )
        # Keep neither the cleartext nor verifier in returned/recorded objects.
        del password, verifier
    except BaseException as error:
        report.update(
            phase="RECOVERY_REQUIRED",
            failed_step=phase,
            error_class=type(error).__name__,
        )
        if isinstance(error, ProvisionStopped):
            report["error_code"] = error.args[0]
        else:
            report["error_code"] = "provisioning_stopped_private_details_not_recorded"
        if commit_attempted and not report["db_transaction_committed"]:
            report["commit_outcome_unknown"] = True
        publish(root / "provision-receipt.json", report)
        return report
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    publish(root / "provision-receipt.json", report)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = apply(args.root)
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "phase",
                        "role_created",
                        "db_transaction_committed",
                        "profile_published",
                        "new_collection",
                    )
                },
                indent=2,
            )
        )
    except Exception as error:
        print(
            json.dumps(
                dict(
                    phase="NOT_STARTED",
                    error_class=type(error).__name__,
                    secret_material_logged=False,
                )
            )
        )
