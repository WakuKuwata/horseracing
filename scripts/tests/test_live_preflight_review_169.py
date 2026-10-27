"""Independent config/ACL attacks. No database, socket, model or collector calls."""

from __future__ import annotations

import json
import os
import stat

import pytest

from scripts.roi_explore.live_preflight_169 import config, database

SECRET = "independent-secret-not-for-output-169"
URL = f"postgresql+psycopg://hr_manual_observer:{SECRET}@127.0.0.1:15432/horseracing"


@pytest.fixture
def valid_env(monkeypatch):
    for key in config.BLOCKED_PG_KEYS:
        monkeypatch.delenv(key, raising=False)
    return dict(
        HORSERACING_OBSERVER_PROFILE_VERSION="169-v1",
        HORSERACING_ENDPOINT_CONFIRMED="true",
        HORSERACING_EXPECTED_DATABASE="horseracing",
        HORSERACING_EXPECTED_ROLE="hr_manual_observer",
        DATABASE_URL=URL,
        HORSERACING_NETKEIBA_MIN_INTERVAL_S="60",
    )


def write_profile(path, values):
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    path.chmod(0o600)
    return path


def test_no_environment_or_owner_only_never_falls_back():
    for environment in ({}, {"DATABASE_URL_OWNER": URL}):
        profile = config.load_profile(environ=environment)
        assert "live_database_url_missing" in profile.problems
        assert profile.url is None
        assert (
            profile.public["can_collect"]
            is profile.public["fallback_used"]
            is profile.public["owner_url_used"]
            is False
        )
        assert SECRET not in json.dumps(profile.public)


def test_valid_explicit_profile_is_only_configuration_validated(valid_env, tmp_path):
    profile = config.load_profile(
        write_profile(tmp_path / "private.env", valid_env), environ={}
    )
    assert profile.problems == []
    assert profile.public["status"] == "PROFILE_VALIDATED_NOT_COLLECTING"
    assert profile.public["can_collect"] is False
    assert SECRET not in repr(profile) and SECRET not in json.dumps(profile.public)
    assert str(profile.url.password) == SECRET


def test_insecure_profile_mode_blocks_even_if_values_valid(valid_env, tmp_path):
    path = write_profile(tmp_path / "public.env", valid_env)
    path.chmod(0o644)
    profile = config.load_profile(path, environ={})
    assert "profile_must_be_private_0600" in profile.problems


def test_profile_symlink_does_not_silently_resolve(valid_env, tmp_path):
    target = write_profile(tmp_path / "real.env", valid_env)
    link = tmp_path / "link.env"
    link.symlink_to(target)
    profile = config.load_profile(link, environ={})
    assert "profile_missing_or_symlink" in profile.problems
    assert profile.url is None


def test_missing_file_does_not_create_or_use_owner(tmp_path):
    target = tmp_path / "not-created.env"
    profile = config.load_profile(target, environ={"DATABASE_URL_OWNER": URL})
    assert profile.url is None and profile.problems
    assert not target.exists()


@pytest.mark.parametrize(
    "key,value,problem",
    [
        (
            "DATABASE_URL",
            URL.replace("127.0.0.1", "db"),
            "explicit_loopback_endpoint_required_no_hostname_rewrite",
        ),
        (
            "DATABASE_URL",
            URL + "?options=-c%20default_transaction_read_only=off",
            "dsn_query_options_require_separate_review",
        ),
        (
            "DATABASE_URL",
            URL.replace(SECRET, "REPLACE_WITH_SECRET"),
            "live_database_url_is_placeholder",
        ),
        (
            "DATABASE_URL",
            URL.replace(SECRET, "${HIDDEN_SECRET}"),
            "live_database_url_is_placeholder",
        ),
        (
            "DATABASE_URL",
            URL.replace("postgresql+psycopg", "postgresql"),
            "native_psycopg_driver_required",
        ),
        ("HORSERACING_EXPECTED_ROLE", "another_role", "expected_login_mismatch"),
        ("HORSERACING_EXPECTED_DATABASE", "another_db", "expected_database_mismatch"),
        (
            "HORSERACING_ENDPOINT_CONFIRMED",
            "false",
            "endpoint_not_explicitly_confirmed",
        ),
        (
            "HORSERACING_NETKEIBA_MIN_INTERVAL_S",
            "NaN",
            "approved_source_interval_missing_or_invalid",
        ),
        (
            "HORSERACING_NETKEIBA_MIN_INTERVAL_S",
            "0.5",
            "approved_source_interval_missing_or_invalid",
        ),
    ],
)
def test_incomplete_or_unsafe_profile_is_not_autorepaired(
    valid_env, key, value, problem
):
    valid_env[key] = value
    profile = config.load_profile(environ=valid_env)
    assert problem in profile.problems
    assert SECRET not in json.dumps(profile.public)


@pytest.mark.parametrize(
    "pg_key",
    ["PGOPTIONS", "PGSERVICE", "PGSERVICEFILE", "PGHOST", "PGUSER", "PGPASSWORD"],
)
def test_libpq_overrides_are_keys_only_and_blocked(valid_env, pg_key):
    valid_env[pg_key] = "sensitive-override-169"
    profile = config.load_profile(environ=valid_env)
    assert "inherited_libpq_overrides_require_removal" in profile.problems
    assert pg_key in profile.public["inherited_pg_override_keys"]
    assert "sensitive-override-169" not in json.dumps(profile.public)


def test_duplicate_and_owner_keys_in_file_are_blocked(valid_env, tmp_path):
    path = write_profile(tmp_path / "private.env", valid_env)
    path.write_text(
        path.read_text() + f"DATABASE_URL={URL}\nDATABASE_URL_OWNER={URL}\n"
    )
    profile = config.load_profile(path, environ={})
    assert "duplicate_profile_key" in profile.problems
    assert "unrecognized_or_owner_keys_in_profile" in profile.problems
    assert SECRET not in json.dumps(profile.public)


def test_environment_context_restores_on_success_and_exception(valid_env, monkeypatch):
    profile = config.load_profile(environ=valid_env)
    monkeypatch.setenv("DATABASE_URL", "original-value-not-a-live-profile")
    before = {key: os.environ.get(key) for key in config.KEYS}
    with pytest.raises(RuntimeError, match="intentional"):
        with config.profile_environment(profile):
            assert os.environ["DATABASE_URL"] == URL
            raise RuntimeError("intentional")
    assert {key: os.environ.get(key) for key in config.KEYS} == before
    with config.profile_environment(profile):
        assert os.environ["HORSERACING_EXPECTED_ROLE"] == "hr_manual_observer"
    assert {key: os.environ.get(key) for key in config.KEYS} == before


def test_late_injected_pgoptions_is_not_used_after_validation(valid_env, monkeypatch):
    profile = config.load_profile(environ=valid_env)
    monkeypatch.setenv("PGOPTIONS", "-c default_transaction_read_only=off")
    with pytest.raises(ValueError):
        with config.profile_environment(profile):
            pytest.fail("late override entered the connection environment")


def test_placeholder_init_is_private_and_never_overwrites(tmp_path):
    path = tmp_path / "private" / "observer.env"
    result = config.init_profile(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert result["placeholder_only"] is True and result["can_collect"] is False
    original = path.read_bytes()
    with pytest.raises(ValueError, match="profile_already_exists"):
        config.init_profile(path)
    assert path.read_bytes() == original
    profile = config.load_profile(path, environ={})
    assert profile.problems and profile.public["can_collect"] is False


def complete_acl():
    role = dict(
        rolname="hr_manual_observer",
        rolcanlogin=True,
        rolsuper=False,
        rolcreatedb=False,
        rolcreaterole=False,
        rolreplication=False,
        rolbypassrls=False,
    )
    base = dict(
        can_connect=True,
        schema_usage=True,
        schema_create=False,
        clock_execute=True,
        snapshot_execute=True,
    )
    tables = {}
    for table, columns in database.CONTRACT["select_relations"].items():
        fields = []
        for column in columns:
            fields.append(
                dict(
                    attname=column,
                    can_read=True,
                    can_insert=table == "fetch_throttle_state"
                    and column in database.CONTRACT["operational_insert_columns"],
                    can_modify=table == "fetch_throttle_state"
                    and column in database.CONTRACT["operational_update_columns"],
                )
            )
        tables[table] = dict(
            metadata=dict(
                relrowsecurity=False,
                relforcerowsecurity=False,
                owned_by_role=False,
                table_delete=False,
                table_truncate=False,
                unqualified_resolves_to_public=True,
                custom_enabled_triggers=0,
                custom_rules=0,
            ),
            columns=fields,
        )
    permissions = dict(role=role["rolname"], base=base, tables=tables, memberships=[])
    identity = dict(
        current_login=role["rolname"],
        session_login=role["rolname"],
        database_name="horseracing",
        standby=False,
        observed_default_readonly="off",
        transaction_readonly="on",
        isolation="repeatable read",
    )
    return role, permissions, identity


def test_sufficient_column_acl_still_does_not_authorize_collection():
    role, permissions, identity = complete_acl()
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=True
    )
    assert result["status"] == "ACL_AND_SETTINGS_PASS_NOW_ONLY"
    assert (
        result["can_collect"]
        is result["operational_write_probe_performed"]
        is result["sequence_required"]
        is False
    )


def test_read_only_acl_is_insufficient_for_throttle_row_lock_and_state():
    role, permissions, identity = complete_acl()
    for row in permissions["tables"]["fetch_throttle_state"]["columns"]:
        row.update(can_insert=False, can_modify=False)
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=True
    )
    assert result["status"] == "BLOCKED"
    assert any("UPDATE" in r for r in result["missing_required"])
    assert any("INSERT" in r for r in result["missing_required"])


@pytest.mark.parametrize(
    "flag",
    ["rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls"],
)
def test_overprivileged_login_is_not_a_live_role(flag):
    role, permissions, identity = complete_acl()
    role[flag] = True
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=True
    )
    assert (
        result["status"] == "BLOCKED"
        and "overprivileged_role:" + flag in result["blocking_reasons"]
    )


@pytest.mark.parametrize(
    "flag",
    [
        "relrowsecurity",
        "relforcerowsecurity",
        "owned_by_role",
        "table_delete",
        "table_truncate",
    ],
)
def test_relation_restrictions_or_overprivilege_are_not_acl_pass(flag):
    role, permissions, identity = complete_acl()
    permissions["tables"]["fetch_throttle_state"]["metadata"][flag] = True
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=True
    )
    assert (
        result["status"] == "BLOCKED"
        and "fetch_throttle_state:" + flag in result["blocking_reasons"]
    )


def test_missing_single_column_is_precisely_identified():
    role, permissions, identity = complete_acl()
    for row in permissions["tables"]["fetch_throttle_state"]["columns"]:
        if row["attname"] == "blocked_until":
            row["can_modify"] = False
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=True
    )
    assert result["missing_required"] == ["fetch_throttle_state.blocked_until:UPDATE"]


@pytest.mark.parametrize(
    "change,reason",
    [
        (
            lambda r, p, i: p["memberships"].append({"granted_role": "group_role"}),
            "role_memberships_require_separate_review",
        ),
        (
            lambda r, p, i: p["tables"]["horses"]["columns"][0].update(can_modify=True),
            "horses:write_privileges_not_needed",
        ),
        (
            lambda r, p, i: p["tables"]["races"]["metadata"].update(
                unqualified_resolves_to_public=False
            ),
            "races:search_path_resolution_unproven",
        ),
        (lambda r, p, i: i.update(standby=True), "standby_or_unknown_server_state"),
        (
            lambda r, p, i: i.update(observed_default_readonly="on"),
            "live_native_default_disallows_operational_writes",
        ),
    ],
)
def test_other_unsafe_acl_or_native_state_blocks(change, reason):
    role, permissions, identity = complete_acl()
    change(role, permissions, identity)
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=True
    )
    assert result["status"] == "BLOCKED" and reason in result["blocking_reasons"]


def test_forced_readonly_inventory_is_not_native_default_measurement():
    role, permissions, identity = complete_acl()
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=False
    )
    assert result["status"] == "BLOCKED"
    assert (
        "live_native_default_not_measured_for_target_profile"
        in result["blocking_reasons"]
    )


def test_unknown_role_does_not_request_a_write_test():
    result = database.evaluate_permissions(
        None, {}, identity={}, native_default_measured=False
    )
    assert result["status"] == "BLOCKED" and result["can_collect"] is False


def test_native_probe_attaches_gate_before_connect_and_rolls_back(
    valid_env, monkeypatch
):
    profile = config.load_profile(environ=valid_env)
    events = []

    class Tx:
        is_active = True

        def rollback(self):
            self.is_active = False
            events.append("rollback")

    class Result:
        def mappings(self):
            return self

        def one(self):
            return dict(
                current_login="hr_manual_observer",
                database_name="horseracing",
                transaction_readonly="on",
                isolation="repeatable read",
                observed_default_readonly="off",
            )

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            events.append("connection_closed")

        def begin(self):
            events.append("begin")
            return Tx()

        def execute(self, statement):
            events.append(str(statement))
            return Result()

    class Engine:
        def connect(self):
            events.append("connect")
            return Conn()

        def dispose(self):
            events.append("dispose")

    def create(*args, **kwargs):
        events.append("create")
        assert kwargs["hide_parameters"] is True and kwargs["echo"] is False
        assert "default_transaction_read_only" not in kwargs["connect_args"]["options"]
        return Engine()

    monkeypatch.setattr(database, "create_engine", create)
    monkeypatch.setattr(
        database, "attach_audit", lambda engine, audit: events.append("attach_audit")
    )
    with pytest.raises(RuntimeError, match="intentional"):
        with database.native_readonly_probe(profile) as (_, identity, audit):
            assert identity["observed_default_readonly"] == "off"
            assert identity["transaction_readonly"] == "on"
            assert audit["database_write"] is audit["new_collection"] is False
            raise RuntimeError("intentional")
    assert events[:5] == [
        "create",
        "attach_audit",
        "connect",
        "begin",
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
    ]
    assert events[-3:] == ["rollback", "connection_closed", "dispose"]
    assert all(
        not str(event).startswith(("INSERT", "UPDATE", "DELETE", "GRANT"))
        for event in events
    )


def test_invalid_profile_is_rejected_before_creating_an_engine(monkeypatch):
    profile = config.load_profile(environ={})

    def forbidden(*args, **kwargs):
        pytest.fail("native engine created for invalid profile")

    monkeypatch.setattr(database, "create_engine", forbidden)
    with pytest.raises(ValueError, match="invalid_live_profile"):
        with database.native_readonly_probe(profile):
            pytest.fail("invalid profile entered")


def test_settings_summary_omits_unknown_secret_values():
    result = database.settings_summary(
        ["search_path=public", "app.private_token=" + SECRET]
    )
    assert result["allowlisted_settings"] == {"search_path": "public"}
    assert result["other_settings_omitted"] == 1
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize(
    "change,reason",
    [
        (
            lambda r, p, i: i.update(current_login="another_role"),
            "role_identity_permission_binding_mismatch",
        ),
        (
            lambda r, p, i: p.update(role="another_role"),
            "role_identity_permission_binding_mismatch",
        ),
        (
            lambda r, p, i: i.update(session_login="another_role"),
            "session_login_differs_from_current_role",
        ),
    ],
)
def test_catalog_acl_and_login_identity_must_match(change, reason):
    role, permissions, identity = complete_acl()
    change(role, permissions, identity)
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=True
    )
    assert result["status"] == "BLOCKED" and reason in result["blocking_reasons"]


def test_invalid_profile_check_has_no_engine_fallback_and_no_secret_outputs(
    tmp_path, monkeypatch
):
    from scripts.roi_explore.live_preflight_169 import run

    path = write_profile(tmp_path / "owner-only.env", {"DATABASE_URL_OWNER": URL})

    def forbidden(*args, **kwargs):
        pytest.fail("invalid configuration attempted a native DB connection")

    monkeypatch.setattr(run, "native_readonly_probe", forbidden)
    out = tmp_path / "result"
    result = run.check(path, out)
    assert result["state"] == "NO_GO" and result["can_collect"] is False
    assert result["native_profile_connection"] == "NOT_RUN_CONFIGURATION_BLOCKED"
    for key in (
        "new_collection",
        "database_write",
        "live_plan_created",
        "model_inference",
        "role_grants_applied",
        "cache_refreshed",
        "slot_reserved",
    ):
        assert result[key] is False
    assert result["observe_invocations"] == 0
    assert result["quote_max_age_seconds"] == 1800
    assert result["both_predictions_durable_before_decision_required"] is True
    assert result["network_boundary"]["attempts"] == []
    for file in out.rglob("*"):
        if file.is_file():
            assert SECRET not in file.read_text()
    assert run.verify(out)["can_collect"] is False


def test_cli_has_no_observe_refresh_grant_or_collection_enable_command():
    import ast
    from scripts.roi_explore.live_preflight_169 import cli

    tree = ast.parse(__import__("inspect").getsource(cli))
    parsers = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
    }
    assert parsers == {"init-profile", "inventory", "check", "verify"}
    arguments = {
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }
    assert "--enable-collection" not in arguments


def make_offline_ready_check(
    tmp_path, monkeypatch, valid_env, *, expires_before_launch=False
):
    import base64
    import contextlib
    import datetime as dt
    import hashlib
    from scripts.roi_explore.live_preflight_169 import run
    from scripts.roi_explore.live_preflight_169.access import ORIGIN

    now = dt.datetime.now(dt.UTC)
    cache = tmp_path / "robots"
    cache.mkdir()
    fetched = (
        now - dt.timedelta(hours=23, minutes=50)
        if expires_before_launch
        else now - dt.timedelta(minutes=1)
    )
    doc = dict(
        schema_version=1,
        origin=ORIGIN,
        outcome="rules",
        status=200,
        fetched_at=fetched.isoformat(),
        body=base64.b64encode(b"User-agent: *\nAllow: /\n").decode(),
    )
    (cache / (hashlib.sha256(ORIGIN.encode()).hexdigest()[:20] + ".json")).write_text(
        json.dumps(doc)
    )
    values = dict(valid_env, HORSERACING_ROBOTS_CACHE_DIR=str(cache))
    profile = write_profile(tmp_path / "observer.env", values)
    role, permissions, identity = complete_acl()
    rows = [
        dict(
            domain="netkeiba.com",
            next_allowed_at=(now - dt.timedelta(seconds=1)).isoformat(),
            updated_at=(now - dt.timedelta(seconds=1)).isoformat(),
            blocked_until=None,
            block_reason=None,
        )
    ]

    class Result:
        def mappings(self):
            return rows

    class Conn:
        def execute(self, *args, **kwargs):
            return Result()

    @contextlib.contextmanager
    def native(_profile):
        yield (
            Conn(),
            identity,
            dict(
                statements=[],
                snapshots=[],
                transactions=[],
                database_write=False,
                new_collection=False,
            ),
        )

    monkeypatch.setattr(run, "native_readonly_probe", native)
    monkeypatch.setattr(
        run, "collect_catalog", lambda conn: dict(non_builtin_roles=[role])
    )
    monkeypatch.setattr(run, "collect_permissions", lambda conn, login: permissions)
    out = tmp_path / "check"
    result = run.check(
        profile,
        out,
        launch_at=now + dt.timedelta(minutes=30) if expires_before_launch else None,
    )
    return result, out, run


def test_offline_ready_facts_never_authorize_collection(
    tmp_path, monkeypatch, valid_env
):
    result, out, run = make_offline_ready_check(tmp_path, monkeypatch, valid_env)
    assert result["offline_configuration_ready_now"] is True
    assert result["access_ready_now"] is True
    assert result["state"] == "OFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING"
    assert result["launch_only_unconfirmed"]
    assert result["can_collect"] is False and result["observe_invocations"] == 0
    assert run.verify(out)["can_collect"] is False


def test_known_robots_expiry_at_planned_launch_is_a_no_go(
    tmp_path, monkeypatch, valid_env
):
    result, _, _ = make_offline_ready_check(
        tmp_path, monkeypatch, valid_env, expires_before_launch=True
    )
    assert result["offline_configuration_ready_now"] is True
    assert result["access_ready_now"] is True
    assert result["robots"]["launch_ready"] is False
    assert result["state"] == "NO_GO" and result["can_collect"] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("observe_invocations", 1),
        ("model_inference", True),
        ("live_plan_created", True),
        ("role_grants_applied", True),
        ("cache_refreshed", True),
        ("slot_reserved", True),
        ("quote_max_age_seconds", 3600),
        ("both_predictions_durable_before_decision_required", False),
    ],
)
def test_manifest_consistent_scope_flag_change_is_rejected(
    tmp_path, monkeypatch, valid_env, key, value
):
    import hashlib

    result, out, run = make_offline_ready_check(tmp_path, monkeypatch, valid_env)
    result[key] = value
    target = out / "result.json"
    target.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"]["result.json"] = hashlib.sha256(target.read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="preflight_fixed_contract_changed"):
        run.verify(out)


def test_dml_timestamp_triggers_on_readonly_base_tables_do_not_block_select():
    role, permissions, identity = complete_acl()
    for table, item in permissions["tables"].items():
        if table != "fetch_throttle_state":
            item["metadata"]["custom_enabled_triggers"] = 1
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=True
    )
    assert result["status"] == "ACL_AND_SETTINGS_PASS_NOW_ONLY"
    assert result["can_collect"] is False


def test_custom_trigger_on_written_throttle_still_requires_review():
    role, permissions, identity = complete_acl()
    permissions["tables"]["fetch_throttle_state"]["metadata"][
        "custom_enabled_triggers"
    ] = 1
    result = database.evaluate_permissions(
        role, permissions, identity=identity, native_default_measured=True
    )
    assert result["status"] == "BLOCKED"
    assert (
        "fetch_throttle_state:custom_trigger_or_rule_requires_review"
        in result["blocking_reasons"]
    )
