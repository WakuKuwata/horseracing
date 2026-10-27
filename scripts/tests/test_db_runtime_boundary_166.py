"""No real database or network is used by these boundary tests."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

from scripts.roi_explore.db_runtime_166 import database as db


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM races",
        'select "race_id", horse_id from race_horses where race_id = %(race_id)s',
        "SELECT current_setting('transaction_read_only') AS read_only",
        "SHOW transaction_isolation",
        "SET TRANSACTION READ ONLY",
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SET LOCAL statement_timeout = 120000",
        "SET LOCAL lock_timeout = '2s'",
    ],
)
def test_read_statements_and_only_reader_settings_pass(sql):
    db.validate_statement(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO races VALUES (1)",
        "UPDATE races SET race_date = now()",
        "DELETE FROM races",
        "CREATE TEMP TABLE copy AS SELECT * FROM races",
        "SELECT * INTO TEMP a FROM races",
        "WITH changed AS (DELETE FROM races RETURNING *) SELECT * FROM changed",
        "SELECT * FROM races FOR UPDATE",
        "SELECT 1; DROP TABLE races",
        "SELECT set_config('default_transaction_read_only','off',false)",
        "SET TRANSACTION READ WRITE",
        "SET default_transaction_read_only = off",
        "SET SESSION AUTHORIZATION postgres",
        "COPY races TO STDOUT",
        "SELECT 1 -- comment",
        "SELECT $$danger$$",
    ],
)
def test_mutations_and_setting_bypass_are_rejected(sql):
    with pytest.raises(ValueError):
        db.validate_statement(sql)


def test_engine_is_lazy_enforces_driver_startup_readonly_and_hides_parameters(
    monkeypatch,
):
    calls, listeners = [], []
    sentinel = object()
    monkeypatch.setattr(
        db,
        "create_engine",
        lambda *args, **kwargs: calls.append((args, kwargs)) or sentinel,
    )
    monkeypatch.setattr(
        db.event,
        "listen",
        lambda engine, name, callback: listeners.append((engine, name, callback)),
    )
    engine, audit = db.create_readonly_engine(
        "postgresql://person:private@localhost:15432/test"
    )
    assert engine is sentinel
    args, kwargs = calls[0]
    assert args[0].drivername == "postgresql+psycopg"
    assert kwargs["hide_parameters"] is True and kwargs["echo"] is False
    assert kwargs["isolation_level"] == "REPEATABLE READ"
    assert "default_transaction_read_only=on" in kwargs["connect_args"]["options"]
    assert kwargs["connect_args"]["connect_timeout"] == 5
    assert kwargs["connect_args"]["hostaddr"] == "127.0.0.1"
    assert kwargs["connect_args"]["application_name"] == "manual166_readonly_runtime"
    assert audit["statements"] == [] and audit["database_write"] is False
    assert "private" not in repr(audit) and "person" not in repr(audit)
    assert {name for _, name, _ in listeners} == {
        "before_cursor_execute",
        "after_cursor_execute",
        "handle_error",
        "rollback",
        "commit",
    }


def test_non_native_driver_rejected_without_factory(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("engine factory reached")

    monkeypatch.setattr(db, "create_engine", forbidden)
    with pytest.raises(ValueError, match="native_postgresql_driver_required"):
        db.create_readonly_engine("sqlite:///ignore.db")


class FakeTransaction:
    is_active = True

    def rollback(self):
        self.is_active = False


class FakeResult:
    def __init__(self, proof):
        self.proof = proof

    def mappings(self):
        return self

    def one(self):
        return self.proof


class FakeConnection:
    def __init__(self, proof):
        self.proof, self.sql, self.tx = proof, [], FakeTransaction()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def begin(self):
        return self.tx

    def execute(self, sql):
        self.sql.append(str(sql))
        return FakeResult(self.proof)


class FakeEngine:
    def __init__(self, proof):
        self.conn = FakeConnection(proof)

    def connect(self):
        return self.conn


def proof(**kwargs):
    return dict(
        default_readonly="on",
        transaction_readonly="on",
        isolation="repeatable read",
        server_version="160013",
        snapshot_id="1:1:",
        **kwargs,
    )


@pytest.mark.parametrize("body_failure", [False, True])
def test_snapshot_proven_and_rollback_on_success_or_exception(body_failure):
    engine, audit = FakeEngine(proof()), dict(snapshots=[], phase="guard")
    if body_failure:
        with pytest.raises(RuntimeError, match="body"):
            with db.readonly_snapshot(engine, audit) as (conn, evidence):
                assert evidence["transaction_readonly"] == "on"
                raise RuntimeError("body")
    else:
        with db.readonly_snapshot(engine, audit) as (conn, evidence):
            assert conn is engine.conn and evidence == proof()
    assert not engine.conn.tx.is_active
    assert engine.conn.sql[0] == "SET TRANSACTION READ ONLY"
    assert audit["snapshots"][0]["rolled_back"] is True
    assert audit["snapshots"][0]["proof"] == proof()


@pytest.mark.parametrize(
    "key,value",
    [
        ("default_readonly", "off"),
        ("transaction_readonly", "off"),
        ("isolation", "read committed"),
    ],
)
def test_snapshot_bad_proof_never_yields(key, value):
    p = proof()
    p[key] = value
    engine, audit = FakeEngine(p), dict(snapshots=[])
    with pytest.raises(ValueError, match="database_snapshot_not_readonly_repeatable"):
        with db.readonly_snapshot(engine, audit):
            raise AssertionError("unsafe snapshot yielded")
    assert not engine.conn.tx.is_active and audit["snapshots"][0]["rolled_back"]


def test_audit_records_execution_shape_but_no_parameters(monkeypatch):
    hooks = {}
    monkeypatch.setattr(
        db.event, "listen", lambda engine, name, fn: hooks.setdefault(name, fn)
    )
    audit = dict(statements=[], transactions=[], phase="features")
    db.attach_audit(object(), audit)
    ctx, cursor = object(), SimpleNamespace(rowcount=19)
    hooks["before_cursor_execute"](
        None,
        cursor,
        "SELECT * FROM races WHERE race_id = %(race_id)s",
        {"race_id": "never-persist-this"},
        ctx,
        False,
    )
    hooks["after_cursor_execute"](None, cursor, "unused", {}, ctx, False)
    row = audit["statements"][0]
    assert (
        row["status"] == "completed"
        and row["rowcount"] == 19
        and row["elapsed_seconds"] >= 0
    )
    assert row["parameter_keys"] == ["race_id"] and row["phase"] == "features"
    assert "never-persist-this" not in repr(audit)
    hooks["rollback"](None)
    assert audit["transactions"][0]["event"] == "rollback"
    connection = FakeConnection(proof())
    hooks["commit"](connection)
    assert audit["transactions"][1]["event"] == "commit_readonly"
    assert audit["transactions"][1]["proof"] == proof()
    assert audit["transactions"][1]["status"] == "verified_before_commit"
    assert connection.sql == [db.PROOF_SQL]


def test_failed_sql_records_class_without_secret_exception(monkeypatch):
    hooks = {}
    monkeypatch.setattr(
        db.event, "listen", lambda engine, name, fn: hooks.setdefault(name, fn)
    )
    audit = dict(statements=[], transactions=[])
    db.attach_audit(object(), audit)
    ctx = object()
    hooks["before_cursor_execute"](None, None, "SELECT 1", (), ctx, False)
    hooks["handle_error"](
        SimpleNamespace(
            execution_context=ctx, original_exception=RuntimeError("private DSN")
        )
    )
    assert audit["statements"][0]["status"] == "failed"
    assert audit["statements"][0]["error_class"] == "RuntimeError"
    assert "private DSN" not in repr(audit)


def test_network_and_collection_entrypoints_blocked_and_restore():
    import urllib.request
    from scripts.roi_explore.manual_observation_165.adapters import ObservationAdapters

    original_urlopen, original_socket = urllib.request.urlopen, socket.create_connection
    with db.database_only() as evidence:
        for call in (
            lambda: socket.create_connection(("example.com", 443)),
            lambda: socket.getaddrinfo("example.com", 443),
            lambda: urllib.request.urlopen("https://example.com"),
            lambda: ObservationAdapters(enabled=True).fetch_quote("202605040907", None),
        ):
            with pytest.raises(db.NetworkBlocked, match="external_collection_disabled"):
                call()
        assert len(evidence["attempts"]) == 4 and evidence["python_network_blocked"]
    assert (
        urllib.request.urlopen is original_urlopen
        and socket.create_connection is original_socket
    )


def test_socket_instance_paths_are_blocked():
    with db.database_only() as evidence:
        with socket.socket() as sock:
            with pytest.raises(db.NetworkBlocked):
                sock.connect(("example.com", 443))
            with pytest.raises(db.NetworkBlocked):
                sock.connect_ex(("example.com", 443))
        assert len(evidence["attempts"]) == 2


def test_psycopg_loopback_resolution_never_calls_python_dns():
    from psycopg._conninfo_attempts import conninfo_attempts

    with db.database_only() as evidence:
        attempts = conninfo_attempts(
            dict(host="localhost", hostaddr="127.0.0.1", port="15432")
        )
    assert attempts == [dict(host="localhost", hostaddr="127.0.0.1", port="15432")]
    assert evidence["attempts"] == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("default_readonly", "off"),
        ("transaction_readonly", "off"),
        ("isolation", "read committed"),
    ],
)
def test_commit_only_after_same_transaction_readonly_proof(monkeypatch, key, value):
    hooks = {}
    monkeypatch.setattr(
        db.event, "listen", lambda engine, name, fn: hooks.setdefault(name, fn)
    )
    audit = dict(statements=[], transactions=[], phase="actual_165_guard")
    db.attach_audit(object(), audit)
    p = proof()
    p[key] = value
    connection = FakeConnection(p)
    with pytest.raises(ValueError, match="database_commit_not_readonly_repeatable"):
        hooks["commit"](connection)
    assert connection.sql == [db.PROOF_SQL]
    assert audit["transactions"][0]["event"] == "commit_rejected"
    assert audit["transactions"][0]["proof"] == p
    assert audit["transactions"][0]["phase"] == "actual_165_guard"


META_SQL = "SELECT pg_catalog.pg_class.relname \nFROM pg_catalog.pg_class JOIN pg_catalog.pg_namespace ON pg_catalog.pg_namespace.oid = pg_catalog.pg_class.relnamespace \nWHERE pg_catalog.pg_class.relname = %(table_name)s::VARCHAR AND pg_catalog.pg_class.relkind = ANY (ARRAY[%(param_1)s::VARCHAR, %(param_2)s::VARCHAR, %(param_3)s::VARCHAR, %(param_4)s::VARCHAR, %(param_5)s::VARCHAR]) AND pg_catalog.pg_table_is_visible(pg_catalog.pg_class.oid) AND pg_catalog.pg_namespace.nspname != %(nspname_1)s::VARCHAR"


def probe_hooks(monkeypatch, query_kind="select"):
    from sqlalchemy import select, literal, text
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

    query = select(literal(1)) if query_kind == "select" else text("SELECT :race_id")
    dialect = PGDialect_psycopg()
    params = dict(
        table_name=query,
        nspname_1="pg_catalog",
        param_1="r",
        param_2="p",
        param_3="f",
        param_4="v",
        param_5="m",
    )
    hooks = {}
    monkeypatch.setattr(
        db.event, "listen", lambda engine, name, fn: hooks.setdefault(name, fn)
    )
    audit = dict(statements=[], transactions=[], phase="load_full_pool")
    db.attach_audit(object(), audit)
    return (
        hooks,
        audit,
        params,
        SimpleNamespace(dialect=dialect),
        str(query.compile(dialect=dialect)),
    )


@pytest.mark.parametrize("query_kind", ["select", "text"])
def test_known_client_adaptation_probe_only_handled_after_immediate_matching_query(
    monkeypatch, query_kind
):
    import psycopg

    hooks, audit, params, context, actual = probe_hooks(monkeypatch, query_kind)
    conn, cursor = object(), SimpleNamespace(rowcount=3)
    hooks["before_cursor_execute"](conn, cursor, META_SQL, params, context, False)
    hooks["handle_error"](
        SimpleNamespace(
            execution_context=context,
            original_exception=psycopg.ProgrammingError("private parameter text"),
        )
    )
    row = audit["statements"][0]
    assert row["status"] == "failed" and row["sqlstate"] is None
    assert row["known_probe_candidate"]["kind"] == db.METADATA_PROBE_KIND
    assert row["parameter_types"]["table_name"].startswith("sqlalchemy.sql.")
    next_context = object()
    hooks["before_cursor_execute"](conn, cursor, actual, {}, next_context, False)
    hooks["after_cursor_execute"](conn, cursor, actual, {}, next_context, False)
    assert row["status"] == "handled_metadata_probe"
    assert row["handled_by_statement_index"] == 1
    assert row["handled_by_query_sha256"] == audit["statements"][1]["sha256"]
    assert "private parameter text" not in repr(audit)
    assert not any(value is params["table_name"] for value in row.values())


@pytest.mark.parametrize(
    "problem",
    [
        "different_sql",
        "different_connection",
        "nonadjacent",
        "different_phase",
        "next_query_failed",
    ],
)
def test_known_probe_requires_immediate_success_on_same_connection_and_phase(
    monkeypatch, problem
):
    import psycopg

    hooks, audit, params, context, actual = probe_hooks(monkeypatch)
    conn, cursor = object(), SimpleNamespace(rowcount=3)
    hooks["before_cursor_execute"](conn, cursor, META_SQL, params, context, False)
    hooks["handle_error"](
        SimpleNamespace(
            execution_context=context,
            original_exception=psycopg.ProgrammingError("adapter"),
        )
    )
    if problem == "different_sql":
        actual = "SELECT 2"
    if problem == "different_connection":
        conn = object()
    if problem == "different_phase":
        audit["phase"] = "another_phase"
    if problem == "nonadjacent":
        other = object()
        hooks["before_cursor_execute"](object(), cursor, "SELECT 2", {}, other, False)
        hooks["after_cursor_execute"](object(), cursor, "SELECT 2", {}, other, False)
    next_context = object()
    hooks["before_cursor_execute"](conn, cursor, actual, {}, next_context, False)
    if problem == "next_query_failed":
        hooks["handle_error"](
            SimpleNamespace(
                execution_context=next_context,
                original_exception=psycopg.ProgrammingError("actual failure"),
            )
        )
    else:
        hooks["after_cursor_execute"](conn, cursor, actual, {}, next_context, False)
    assert audit["statements"][0]["status"] == "failed"
    assert "handled_by_query_sha256" not in audit["statements"][0]


@pytest.mark.parametrize(
    "problem",
    [
        "server_sqlstate",
        "wrong_type",
        "extra_parameter",
        "wrong_sql",
        "wrong_error_class",
    ],
)
def test_no_broad_programmingerror_or_metadata_exception_exemption(
    monkeypatch, problem
):
    import psycopg

    hooks, audit, params, context, actual = probe_hooks(monkeypatch)
    conn, cursor = object(), SimpleNamespace(rowcount=3)
    failure = psycopg.ProgrammingError("adapter")
    sql = META_SQL
    if problem == "server_sqlstate":
        failure = psycopg.errors.SyntaxError("server error")
    if problem == "wrong_type":
        params["table_name"] = "plain_table_name"
    if problem == "extra_parameter":
        params["unrecognized"] = "secret"
    if problem == "wrong_sql":
        sql = "SELECT 1"
    if problem == "wrong_error_class":
        failure = RuntimeError("private failure")
    hooks["before_cursor_execute"](conn, cursor, sql, params, context, False)
    hooks["handle_error"](
        SimpleNamespace(execution_context=context, original_exception=failure)
    )
    row = audit["statements"][0]
    assert row["status"] == "failed" and "known_probe_candidate" not in row
    if problem == "server_sqlstate":
        assert row["sqlstate"] == "42601"
    next_context = object()
    hooks["before_cursor_execute"](conn, cursor, actual, {}, next_context, False)
    hooks["after_cursor_execute"](conn, cursor, actual, {}, next_context, False)
    assert row["status"] == "failed"


def test_metadata_probe_cannot_be_resolved_in_a_later_transaction(monkeypatch):
    import psycopg

    hooks, audit, params, context, actual = probe_hooks(monkeypatch)
    conn, cursor = object(), SimpleNamespace(rowcount=3)
    hooks["before_cursor_execute"](conn, cursor, META_SQL, params, context, False)
    hooks["handle_error"](
        SimpleNamespace(
            execution_context=context,
            original_exception=psycopg.ProgrammingError("adapter"),
        )
    )
    hooks["rollback"](conn)
    next_context = object()
    hooks["before_cursor_execute"](conn, cursor, actual, {}, next_context, False)
    hooks["after_cursor_execute"](conn, cursor, actual, {}, next_context, False)
    assert audit["statements"][0]["status"] == "failed"
