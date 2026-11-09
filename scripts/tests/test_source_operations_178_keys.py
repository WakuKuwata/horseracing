"""Feature 178 (T069): the six optional JRDB/netkeiba policy keys pass the 172 wrapper's startup
validation at their defaults, their values are typed, and unknown keys are still rejected."""

import json

import pytest
from scripts.source_operations_172 import (
    CONFIG,
    JRDB_BOOL_KEYS,
    JRDB_INTERVAL_KEY,
    NEAR_POST_SOURCE_KEY,
    load_policy,
)


def policy_file(tmp_path, **changes):
    value = json.loads(CONFIG.read_text())
    value.update(changes)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    return path


def test_the_committed_policy_carries_the_six_keys_at_their_defaults():
    policy = load_policy(CONFIG)
    assert policy["jrdb_enabled"] is False and policy["netkeiba_enabled"] is True
    assert policy["jra_emergency_entries"] is False and policy["jrdb_archive_blackout"] is True
    assert policy[JRDB_INTERVAL_KEY] == 5 and policy[NEAR_POST_SOURCE_KEY] == "netkeiba"


def test_the_keys_are_optional(tmp_path):
    doc = json.loads(CONFIG.read_text())
    for key in JRDB_BOOL_KEYS | {JRDB_INTERVAL_KEY, NEAR_POST_SOURCE_KEY}:
        doc.pop(key, None)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(doc))
    assert "jrdb_enabled" not in load_policy(path)


@pytest.mark.parametrize("key", sorted(JRDB_BOOL_KEYS))
@pytest.mark.parametrize("value", ["true", 1, 0, None, [True]])
def test_boolean_keys_must_be_booleans(tmp_path, key, value):
    with pytest.raises(ValueError, match=f"{key}_not_boolean"):
        load_policy(policy_file(tmp_path, **{key: value}))


@pytest.mark.parametrize("value", [0, 1, 1.9, True, "5", None, float("nan"), float("inf")])
def test_jrdb_interval_is_a_finite_number_at_least_two(tmp_path, value):
    with pytest.raises(ValueError, match="jrdb_interval_invalid"):
        load_policy(policy_file(tmp_path, **{JRDB_INTERVAL_KEY: value}))


@pytest.mark.parametrize("value", ["both", "", None, 1])
def test_near_post_source_is_one_of_two_names(tmp_path, value):
    with pytest.raises(ValueError, match="near_post_source_invalid"):
        load_policy(policy_file(tmp_path, **{NEAR_POST_SOURCE_KEY: value}))


def test_netkeiba_can_be_stopped_by_policy(tmp_path):
    policy = load_policy(
        policy_file(tmp_path, netkeiba_enabled=False, near_post_source="jra_official")
    )
    assert policy["netkeiba_enabled"] is False and policy[NEAR_POST_SOURCE_KEY] == "jra_official"


def test_unknown_keys_are_still_rejected(tmp_path):
    with pytest.raises(ValueError, match="policy_keys_invalid"):
        load_policy(policy_file(tmp_path, jrdb_enable=True))
