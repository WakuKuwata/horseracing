"""GET /ops/v1/status (B5): reports what THIS process enforces, separately from the policy file."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from horseracing_ops.routers import status as st

DUMMY_DB = "postgresql+psycopg://u:p@127.0.0.1:1/none"  # engine creation is lazy; never connects


def _policy(tmp_path, **overrides):
    body = {"schema_version": "172-v1", "source_interval_seconds": 60,
            "ops_fetch_min_interval_seconds": 60, "shared_limiter_enabled": True,
            "collection_paused": True}
    body.update(overrides)
    path = tmp_path / "source-operations.json"
    path.write_text(json.dumps(body))
    return path


def test_paused_follows_the_env_the_wrapper_sets(tmp_path):
    path = _policy(tmp_path)
    paused = st.build_status(environ={st.COLLECTION_MODE_ENV: "paused"}, policy_path=path,
                             fetch_min_interval=60)
    assert paused.collection_paused is True and paused.writes_accepted is False
    assert paused.policy_mismatch is False
    assert paused.detail == st.PAUSED_DETAIL
    assert paused.policy_file.collection_paused is True
    assert paused.policy_file.source_interval_seconds == 60


def test_unwrapped_process_does_not_claim_to_pause_but_flags_the_mismatch(tmp_path):
    path = _policy(tmp_path)
    live = st.build_status(environ={}, policy_path=path, fetch_min_interval=1.0)
    assert live.collection_paused is False and live.writes_accepted is True
    # The file says paused, this process does not enforce it: say so instead of either lie.
    assert live.policy_mismatch is True
    assert live.detail == st.ACTIVE_DETAIL
    assert live.ops_fetch_min_interval_seconds == 1.0


@pytest.mark.parametrize("mode", ["", "active", "PAUSE", "0"])
def test_only_the_exact_paused_mode_pauses(tmp_path, mode):
    path = _policy(tmp_path, collection_paused=False)
    out = st.build_status(environ={st.COLLECTION_MODE_ENV: mode}, policy_path=path,
                          fetch_min_interval=1)
    assert out.collection_paused is False and out.policy_mismatch is False
    assert out.collection_mode == mode


def test_missing_or_malformed_policy_file_is_data_not_an_error(tmp_path):
    missing = st.build_status(environ={}, policy_path=tmp_path / "nope.json", fetch_min_interval=1)
    assert missing.policy_file.present is False and missing.policy_mismatch is False
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    out = st.build_status(environ={}, policy_path=broken, fetch_min_interval=1)
    assert out.policy_file.present is True and out.policy_file.parse_error == "JSONDecodeError"
    assert out.policy_file.collection_paused is None
    # wrong types become null rather than a guess (bool is not a number, string is not a bool)
    typed = _policy(tmp_path, collection_paused="true", source_interval_seconds=True)
    out = st.build_status(environ={}, policy_path=typed, fetch_min_interval=1)
    assert out.policy_file.collection_paused is None
    assert out.policy_file.source_interval_seconds is None


def test_endpoint_is_a_get_that_reads_env_at_request_time(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DUMMY_DB)
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    monkeypatch.setenv(st.POLICY_PATH_ENV, str(_policy(tmp_path)))
    monkeypatch.delenv(st.COLLECTION_MODE_ENV, raising=False)
    from horseracing_ops.app import app
    with TestClient(app) as client:
        assert client.get("/ops/v1/status").json()["collection_paused"] is False
        monkeypatch.setenv(st.COLLECTION_MODE_ENV, "paused")
        body = client.get("/ops/v1/status").json()
    assert body["collection_paused"] is True and body["writes_accepted"] is False
    assert body["policy_file"]["schema_version"] == "172-v1"
    assert body["detail"] == st.PAUSED_DETAIL
