"""Provision logic tests without actual roles, passwords, database writes or network."""

from __future__ import annotations

import json

import pytest
from psycopg import sql
from scripts.roi_explore.provision_observer_170 import apply as provision
from scripts.roi_explore.live_preflight_169.config import load_profile


def test_profile_roundtrip_does_not_return_credentials(tmp_path):
    secret = "synthetic_test_secret_for_170"
    path = tmp_path / "private.env"
    path.write_bytes(provision.profile_body(secret, 60))
    path.chmod(0o600)
    result = load_profile(path, environ={})
    assert not result.problems and result.url.password == secret
    assert secret not in repr(result) and secret not in json.dumps(result.public)
    assert result.interval == 60


@pytest.mark.parametrize(
    "key",
    [
        "database_name",
        "database_oid",
        "system_identifier",
        "server_address",
        "server_port",
    ],
)
def test_identity_mismatch_is_never_a_target(key):
    expected = dict(
        database_name="horseracing",
        database_oid="11",
        system_identifier="22",
        server_address="127.0.0.1",
        server_port=5432,
        standby=False,
    )
    actual = dict(expected)
    actual[key] = "different"
    assert not provision.match_identity(actual, expected)


def test_standby_is_not_writable_provision_target():
    expected = dict(
        database_name="horseracing",
        database_oid="11",
        system_identifier="22",
        server_address="127.0.0.1",
        server_port=5432,
        standby=False,
    )
    assert not provision.match_identity(dict(expected, standby=True), expected)


class Result:
    def __init__(self, one=None, rows=None):
        self.one = one
        self.rows = rows

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Connection:
    def __init__(self):
        self.commands = []

    def execute(self, statement, params=None):
        rendered = (
            statement.as_string()
            if isinstance(statement, sql.Composable)
            else statement
        )
        self.commands.append((rendered, params))
        if rendered.startswith("SELECT has_function_privilege"):
            return Result(one=(True,))
        raise_unexpected = rendered.startswith("SELECT")
        assert not raise_unexpected, rendered
        return Result()


def test_grants_only_contract_columns_and_no_role_rotation():
    contract = json.loads(
        (
            provision.ROOT
            / "specs/169-live-preflight/evidence/permission-contract.json"
        ).read_text()
    )
    conn = Connection()
    assert provision.grant_contract(conn, contract) == []
    commands = [x[0] for x in conn.commands]
    reads = [x for x in commands if x.startswith("GRANT SELECT")]
    assert len(reads) == 6 and all(x.startswith("GRANT SELECT (") for x in reads)
    assert sum(x.startswith("GRANT INSERT (") for x in commands) == 1
    assert sum(x.startswith("GRANT UPDATE (") for x in commands) == 1
    assert not any(
        "GRANT DELETE" in x or "ON ALL TABLES" in x or "ON SEQUENCE" in x
        for x in commands
    )
    assert not any("PASSWORD" in x for x in commands)
    assert not any('TO "aiuma"' in x for x in commands)


@pytest.mark.parametrize(
    "mutation",
    ["log_statement", "transaction_sampling", "preload", "event_trigger", "extension"],
)
def test_secret_sql_requires_all_logging_paths_disabled(mutation):
    class LogConnection:
        def execute(self, statement, params=None):
            if statement.startswith(
                "SELECT name,setting FROM pg_settings WHERE name = ANY"
            ):
                settings = dict(provision.LOG_SETTINGS)
                if mutation == "log_statement":
                    settings["log_statement"] = "all"
                if mutation == "transaction_sampling":
                    settings["log_transaction_sample_rate"] = "1"
                return Result(rows=list(settings.items()))
            if statement.startswith(
                "SELECT name,setting FROM pg_settings WHERE name IN"
            ):
                return Result(
                    rows=[
                        (
                            "shared_preload_libraries",
                            "pgaudit" if mutation == "preload" else "",
                        )
                    ]
                )
            if statement.startswith("SELECT count(*) FROM pg_event_trigger"):
                return Result(one=(1 if mutation == "event_trigger" else 0,))
            return Result(
                rows=[("plpgsql",)]
                + ([("pgaudit",)] if mutation == "extension" else [])
            )

    with pytest.raises(provision.ProvisionStopped):
        provision._log_guard(LogConnection())


def test_actual_role_or_secret_is_not_created_before_target_confirmation(tmp_path):
    with pytest.raises(provision.ProvisionStopped, match="target_confirmation_missing"):
        provision.apply(tmp_path)
    assert list(tmp_path.iterdir()) == []
