"""Feature 176: the optional ``jra_official_interval_seconds`` policy key (reader first, FR-004)."""

import json
import os

import pytest

from scripts.source_operations_172 import CONFIG, JRA_INTERVAL_KEY, apply_policy, load_policy

ENV = "HORSERACING_JRA_MIN_INTERVAL_S"


def policy_file(tmp_path, **changes):
    value = json.loads(CONFIG.read_text())
    value.update(changes)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    return path


def test_key_is_optional_and_absent_means_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV, raising=False)
    doc = json.loads(CONFIG.read_text())
    doc.pop(JRA_INTERVAL_KEY, None)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(doc))
    policy = load_policy(path)
    assert JRA_INTERVAL_KEY not in policy
    apply_policy(policy)
    assert (
        ENV not in os.environ
    )  # the JRA CLI then refuses to start by itself (policy.validate_startup)


@pytest.mark.parametrize("value", [10, 10.0, 15, 60.5])
def test_valid_interval_is_exported_for_the_jra_cli(monkeypatch, tmp_path, value):
    monkeypatch.delenv(ENV, raising=False)
    policy = load_policy(policy_file(tmp_path, **{JRA_INTERVAL_KEY: value}))
    apply_policy(policy)
    assert os.environ[ENV] == str(value)
    # the netkeiba interval is untouched by the JRA key
    assert os.environ["HORSERACING_NETKEIBA_MIN_INTERVAL_S"] == str(
        policy["source_interval_seconds"]
    )


@pytest.mark.parametrize(
    "value", [0, 1, 9.99, True, False, "10", None, float("nan"), float("inf"), [10]]
)
def test_invalid_interval_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        load_policy(policy_file(tmp_path, **{JRA_INTERVAL_KEY: value}))


def test_unknown_keys_still_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_policy(policy_file(tmp_path, jra_interval_seconds=10))
