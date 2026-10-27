"""Read-only PostgreSQL access while all Python network collection is disabled.

The database driver uses libpq's native socket implementation. Python socket/HTTP APIs are
blocked during the benchmark; no scraper or collector is enabled by this module.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib
import importlib.util
import os
import re
import socket
import time
from unittest.mock import patch

from sqlalchemy import create_engine, event, text
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.sql.selectable import Select
from sqlalchemy.engine import make_url

LOCAL_URL = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 -c lock_timeout=2000"
)
PROOF_SQL = """SELECT current_setting('default_transaction_read_only') AS default_readonly,
current_setting('transaction_read_only') AS transaction_readonly,
current_setting('transaction_isolation') AS isolation,
current_setting('server_version_num') AS server_version,
txid_current_snapshot()::text AS snapshot_id"""


METADATA_PROBE_SQL_SHA256 = (
    "3abe6a141a1bbb7060bedef89c62e34e818f1bc921dfa7aaab17b640e38baa37"
)
METADATA_PROBE_KIND = "pandas_read_sql_has_table_clause_adaptation"
METADATA_PROBE_PARAMETER_KEYS = {
    "nspname_1",
    "param_1",
    "param_2",
    "param_3",
    "param_4",
    "param_5",
    "table_name",
}


class NetworkBlocked(RuntimeError):
    """A collection/network path was attempted outside the native database driver."""


def _now():
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds")


def _parameter_keys(parameters):
    # Values may contain private material. Never record them or stringify an arbitrary object.
    if isinstance(parameters, dict):
        return sorted(str(key) for key in parameters)
    if isinstance(parameters, (tuple, list)):
        return [str(index) for index in range(len(parameters))]
    return []


def _type_name(value):
    cls = type(value)
    return cls.__module__ + "." + cls.__qualname__


def _parameter_types(parameters):
    if isinstance(parameters, dict):
        return {str(key): _type_name(value) for key, value in parameters.items()}
    if isinstance(parameters, (tuple, list)):
        return {str(index): _type_name(value) for index, value in enumerate(parameters)}
    return {}


def _probe_candidate(statement, parameters, execution_context):
    # pandas 3 read_sql probes has_table even for a SQLAlchemy clause. psycopg rejects
    # binding that clause as a table name before sending a server query. Only this exact
    # dependency query/parameter shape is eligible for a later handled classification.
    if (
        hashlib.sha256(statement.encode()).hexdigest() != METADATA_PROBE_SQL_SHA256
        or not isinstance(parameters, dict)
        or set(parameters) != METADATA_PROBE_PARAMETER_KEYS
        or type(parameters["table_name"]) not in (Select, TextClause)
        or any(
            type(value) is not str
            for key, value in parameters.items()
            if key != "table_name"
        )
    ):
        return None
    try:
        expected = str(
            parameters["table_name"].compile(dialect=execution_context.dialect)
        )
    except Exception:
        return None
    return dict(
        kind=METADATA_PROBE_KIND,
        expected_query_sha256=hashlib.sha256(expected.encode()).hexdigest(),
        query_parameter_type=_type_name(parameters["table_name"]),
    )


def validate_statement(statement):
    """Permit only read queries and the precise transaction settings used by the reader.

    PostgreSQL read-only defaults are the enforcement layer from connection creation. This
    extra gate also excludes DDL, temporary writes, writable CTEs and settings that turn it off.
    """
    if not isinstance(statement, str):
        raise ValueError("sql_statement_invalid")
    # Comments and quoted strings cannot change the statement class. Reject comments rather
    # than attempting a SQL grammar that might accidentally admit a second statement.
    if "--" in statement or "/*" in statement or "*/" in statement:
        raise ValueError("sql_comments_not_permitted")
    bare = re.sub(r"'(?:''|[^'])*'", "''", statement)
    bare = re.sub(r'"(?:""|[^"])*"', '""', bare)
    bare = bare.strip().rstrip(";").strip()
    if ";" in bare or "$" in bare:
        raise ValueError("sql_multiple_or_dollar_statement_rejected")
    normalized = " ".join(bare.upper().split())
    if normalized.startswith(("SELECT ", "SHOW ")):
        if re.search(
            r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|COPY|CALL|DO|"
            r"GRANT|REVOKE|INTO|SET_CONFIG|LO_EXPORT|DBLINK_EXEC|PG_TERMINATE_BACKEND|"
            r"PG_CANCEL_BACKEND)\b",
            normalized,
        ):
            raise ValueError("sql_mutation_rejected")
        return
    # The first command in every reader transaction is its explicit READ ONLY setting.
    if normalized in (
        "SET TRANSACTION READ ONLY",
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
    ):
        return
    if re.fullmatch(
        r"SET LOCAL (?:STATEMENT_TIMEOUT|LOCK_TIMEOUT)\s*=\s*(?:[0-9]+|'')", normalized
    ):
        return
    raise ValueError("sql_statement_not_readonly")


def attach_audit(engine, audit):
    """Record SQL shape, elapsed execution and transaction lifecycle without parameter values."""
    active, pending = {}, {}

    def before(conn, cursor, statement, parameters, context, executemany):
        validate_statement(statement)
        row = dict(
            sql=statement,
            sha256=hashlib.sha256(statement.encode()).hexdigest(),
            parameter_keys=_parameter_keys(parameters),
            parameter_types=_parameter_types(parameters),
            sqlstate=None,
            phase=audit.get("phase", "unassigned"),
            started_at=_now(),
            status="executing",
            elapsed_seconds=None,
            rowcount=None,
        )
        audit["statements"].append(row)
        index = len(audit["statements"]) - 1
        active[id(context)] = (
            time.perf_counter(),
            row,
            _probe_candidate(statement, parameters, context),
            id(conn),
            index,
        )

    def after(conn, cursor, statement, parameters, context, executemany):
        item = active.pop(id(context), None)
        if item is not None:
            started, row, _, connection_id, index = item
            row.update(
                status="completed",
                completed_at=_now(),
                elapsed_seconds=time.perf_counter() - started,
                rowcount=cursor.rowcount,
            )
            prior = pending.pop(connection_id, None)
            if prior is not None:
                probe_row, probe_index = prior
                candidate = probe_row["known_probe_candidate"]
                if (
                    index == probe_index + 1
                    and row["sha256"] == candidate["expected_query_sha256"]
                    and row["phase"] == probe_row["phase"]
                ):
                    probe_row.update(
                        status="handled_metadata_probe",
                        handled_by_query_sha256=row["sha256"],
                        handled_by_statement_index=index,
                    )

    def error(context):
        item = active.pop(id(context.execution_context), None)
        if item is not None:
            started, row, candidate, connection_id, index = item
            pending.pop(connection_id, None)
            original = context.original_exception
            row.update(
                status="failed",
                completed_at=_now(),
                elapsed_seconds=time.perf_counter() - started,
                error_class=type(original).__name__,
                sqlstate=getattr(original, "sqlstate", None),
            )
            if (
                candidate is not None
                and _type_name(original) == "psycopg.ProgrammingError"
                and row["sqlstate"] is None
            ):
                row["known_probe_candidate"] = candidate
                pending[connection_id] = (row, index)

    def rollback(conn):
        pending.pop(id(conn), None)
        audit["transactions"].append(
            dict(event="rollback", at=_now(), phase=audit.get("phase", "unassigned"))
        )

    def commit(conn):
        pending.pop(id(conn), None)
        # The unchanged 165 guard uses `with conn.begin()`: its successful READ ONLY
        # transaction ends with COMMIT. Verify settings in that SAME transaction before
        # allowing the native commit. This does not authorize any write statement.
        proof = dict(conn.execute(text(PROOF_SQL)).mappings().one())
        safe = (
            proof["default_readonly"] == "on"
            and proof["transaction_readonly"] == "on"
            and proof["isolation"] == "repeatable read"
        )
        audit["transactions"].append(
            dict(
                event="commit_readonly" if safe else "commit_rejected",
                at=_now(),
                phase=audit.get("phase", "unassigned"),
                proof=proof,
                status="verified_before_commit" if safe else "rejected",
            )
        )
        if not safe:
            raise ValueError("database_commit_not_readonly_repeatable")

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "handle_error", error)
    event.listen(engine, "rollback", rollback)
    event.listen(engine, "commit", commit)


def create_readonly_engine(url=None):
    """Return a lazy native-PostgreSQL engine and an audit ledger, without opening a connection."""
    resolved = url if url is not None else os.getenv("DATABASE_URL", LOCAL_URL)
    parsed = make_url(resolved)
    if parsed.drivername not in ("postgresql+psycopg", "postgresql"):
        raise ValueError("native_postgresql_driver_required")
    # Bare PostgreSQL URLs otherwise choose psycopg2. Fix the native driver, never print the URL.
    parsed = parsed.set(drivername="postgresql+psycopg")
    connect_args = dict(
        connect_timeout=5,
        application_name="manual166_readonly_runtime",
        options=OPTIONS,
    )
    # psycopg 3 resolves hostnames through Python getaddrinfo before libpq. Pin the
    # repository's loopback hostname without DNS so the network prohibition remains total.
    if parsed.host == "localhost":
        connect_args["hostaddr"] = "127.0.0.1"
    engine = create_engine(
        parsed,
        isolation_level="REPEATABLE READ",
        hide_parameters=True,
        echo=False,
        connect_args=connect_args,
    )
    audit = dict(
        database_write=False,
        new_collection=False,
        phase="initialization",
        statements=[],
        snapshots=[],
        transactions=[],
    )
    attach_audit(engine, audit)
    return engine, audit


@contextlib.contextmanager
def readonly_snapshot(engine, audit):
    """Open and prove a new consistent read-only snapshot; finish with rollback on every path."""
    record = dict(
        started_at=_now(), phase=audit.get("phase", "unassigned"), rolled_back=False
    )
    audit["snapshots"].append(record)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            proof = dict(conn.execute(text(PROOF_SQL)).mappings().one())
            if not (
                proof["default_readonly"] == "on"
                and proof["transaction_readonly"] == "on"
                and proof["isolation"] == "repeatable read"
            ):
                raise ValueError("database_snapshot_not_readonly_repeatable")
            record["proof"] = proof
            yield conn, proof
        finally:
            if transaction.is_active:
                transaction.rollback()
            record.update(rolled_back=True, finished_at=_now())


@contextlib.contextmanager
def database_only():
    """Forbid collection entry points and Python sockets; libpq database access stays available."""
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(dict(at=_now()))
        raise NetworkBlocked("external_collection_disabled")

    with contextlib.ExitStack() as stack:
        for owner, attr in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
        ):
            stack.enter_context(patch.object(owner, attr, blocked))
        for module_name, owner_name, attr in (
            ("urllib.request", None, "urlopen"),
            ("urllib.request", "OpenerDirector", "open"),
            ("http.client", "HTTPConnection", "connect"),
            ("http.client", "HTTPSConnection", "connect"),
            ("requests.sessions", "Session", "request"),
            ("httpx", "Client", "request"),
            ("httpx", "AsyncClient", "request"),
            (
                "scripts.roi_explore.manual_observation_165.adapters",
                "ObservationAdapters",
                "fetch_quote",
            ),
        ):
            if importlib.util.find_spec(module_name) is None:
                continue
            module = importlib.import_module(module_name)
            owner = getattr(module, owner_name) if owner_name else module
            stack.enter_context(patch.object(owner, attr, blocked))
        yield dict(new_collection=False, python_network_blocked=True, attempts=attempts)
