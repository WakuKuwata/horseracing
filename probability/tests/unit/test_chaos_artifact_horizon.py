"""Feature 086 fail-closed primary-horizon and manifest contracts."""

from __future__ import annotations

import copy
import datetime
import json
from pathlib import Path

import pytest
from horseracing_eval.stage_discount import StageDiscount

import horseracing_probability.chaos_artifact as chaos_artifact
from horseracing_probability.chaos_artifact import (
    ChaosArtifactApprovalError,
    ChaosArtifactEdgesError,
    ChaosArtifactManifestError,
    ChaosArtifactPrimaryHorizonError,
    approved_digests_from_manifest,
    compute_chaos_artifact_digest,
    load_chaos_artifact,
    resolve_current_digest,
    upgrade_legacy_artifact_horizon,
)
from horseracing_probability.chaos_distribution import chaos_readout
from horseracing_probability.chaos_events import CHAOS_EVENTS_V1
from horseracing_probability.market_odds import market_implied_win_probs
from tests.unit.test_chaos_artifact import _TARGET_2026, _payload, _redigest, _write

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ARTIFACT_DIR = _REPO_ROOT / "artifacts" / "chaos_bands"
_PREVIOUS_DIGEST = "e782c255adde487e200c5814a61962ffc8c87709811b4b9511c223f1c33b8d8f"
_CURRENT_DIGEST = "20d1e000de200a2a1ad0687ba9456cf12121f1b575dc5d87a7d482e9f9f83680"
_INVARIANT_FIELDS = (
    "lambda2",
    "lambda3",
    "quintile_edges",
    "fit_input_hash",
    "race_set_hash",
    "fit_through",
    "valid_from",
    "band_axis",
    "s_threshold_basis",
    "version",
)


def _manifest(tmp_path, entries: list[dict[str, str]], monkeypatch) -> None:
    path = tmp_path / "approved.json"
    path.write_text(json.dumps({"approved": entries}), encoding="utf-8")
    monkeypatch.setenv(chaos_artifact.CHAOS_APPROVED_MANIFEST_ENV, str(path))


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda horizon: None, "primary_horizon is required"),
        (
            lambda horizon: horizon.__setitem__("primary_horizon", []),
            "primary_horizon must be a mapping",
        ),
        (
            lambda horizon: horizon["primary_horizon"].__setitem__(
                "minimum_seconds_to_post", "600"
            ),
            "minimum_seconds_to_post must be a non-negative integer",
        ),
        (
            lambda horizon: horizon["primary_horizon"].__setitem__(
                "minimum_seconds_to_post", -1
            ),
            "minimum_seconds_to_post must be a non-negative integer",
        ),
        (
            lambda horizon: horizon["primary_horizon"].__setitem__(
                "maximum_seconds_to_post", None
            ),
            "maximum_seconds_to_post must be an integer",
        ),
        (
            lambda horizon: horizon["primary_horizon"].__setitem__(
                "maximum_seconds_to_post", "86400"
            ),
            "maximum_seconds_to_post must be an integer",
        ),
        (
            lambda horizon: horizon["primary_horizon"].__setitem__(
                "maximum_seconds_to_post", 599
            ),
            "maximum_seconds_to_post must be greater",
        ),
        (
            lambda horizon: horizon["primary_horizon"].__setitem__(
                "maximum_seconds_to_post", 600
            ),
            "maximum_seconds_to_post must be greater",
        ),
        (
            lambda horizon: horizon["primary_horizon"].__setitem__(
                "min_seconds_to_post", 600
            ),
            "short primary_horizon aliases are forbidden",
        ),
        (
            lambda horizon: horizon["primary_horizon"].__setitem__(
                "max_seconds_to_post", 86_400
            ),
            "short primary_horizon aliases are forbidden",
        ),
    ),
    ids=(
        "missing",
        "not-mapping",
        "minimum-not-int",
        "minimum-negative",
        "maximum-null",
        "maximum-not-int",
        "maximum-below-minimum",
        "maximum-equals-minimum",
        "short-minimum-alias",
        "short-maximum-alias",
    ),
)
def test_loader_rejects_every_invalid_primary_horizon_case(tmp_path, mutate, message):
    payload = _payload()
    if mutate(payload["preregistration"]) is None and "primary_horizon" in payload[
        "preregistration"
    ]:
        # The no-op mutation is the explicit missing-field case.
        if message == "primary_horizon is required":
            del payload["preregistration"]["primary_horizon"]
    _redigest(payload)
    path = _write(tmp_path, payload)

    with pytest.raises(ChaosArtifactPrimaryHorizonError, match=message):
        load_chaos_artifact(
            path,
            approved_digests={payload["artifact_digest"]},
            target_date=_TARGET_2026,
        )


def test_loader_does_not_impose_a_display_driven_upper_bound_on_minimum(tmp_path):
    payload = _payload()
    payload["preregistration"]["primary_horizon"] = {
        "minimum_seconds_to_post": 90_000,
        "maximum_seconds_to_post": 100_000,
    }
    _redigest(payload)

    artifact = load_chaos_artifact(
        _write(tmp_path, payload),
        approved_digests={payload["artifact_digest"]},
        target_date=_TARGET_2026,
    )

    assert artifact.preregistration["primary_horizon"]["minimum_seconds_to_post"] == 90_000


def test_upgrade_rejects_an_unapproved_digest(tmp_path, monkeypatch):
    payload = _payload()
    del payload["preregistration"]["primary_horizon"]
    _redigest(payload)
    _manifest(
        tmp_path,
        [{"digest": "f" * 64, "status": "active"}],
        monkeypatch,
    )

    with pytest.raises(ChaosArtifactApprovalError, match="not listed"):
        upgrade_legacy_artifact_horizon(
            _write(tmp_path, payload),
            expected_digest=payload["artifact_digest"],
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=86_400,
        )


def test_upgrade_rejects_a_malformed_expected_digest(tmp_path, monkeypatch):
    payload = _payload()
    del payload["preregistration"]["primary_horizon"]
    _redigest(payload)
    _manifest(tmp_path, [{"digest": payload["artifact_digest"], "status": "active"}], monkeypatch)

    with pytest.raises(ChaosArtifactApprovalError, match="lowercase SHA-256"):
        upgrade_legacy_artifact_horizon(
            _write(tmp_path, payload),
            expected_digest="not-a-digest",
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=86_400,
        )


def test_upgrade_runs_non_horizon_validation_on_the_legacy_payload(tmp_path, monkeypatch):
    payload = _payload()
    del payload["preregistration"]["primary_horizon"]
    payload["quintile_edges"] = [0.1, 0.1, 0.2, 0.3]
    _redigest(payload)
    _manifest(tmp_path, [{"digest": payload["artifact_digest"], "status": "active"}], monkeypatch)

    with pytest.raises(ChaosArtifactEdgesError):
        upgrade_legacy_artifact_horizon(
            _write(tmp_path, payload),
            expected_digest=payload["artifact_digest"],
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=86_400,
        )


def test_upgrade_rejects_an_artifact_that_already_has_a_horizon(tmp_path, monkeypatch):
    payload = _payload()
    _manifest(tmp_path, [{"digest": payload["artifact_digest"], "status": "active"}], monkeypatch)

    with pytest.raises(ChaosArtifactPrimaryHorizonError, match="legacy artifact without"):
        upgrade_legacy_artifact_horizon(
            _write(tmp_path, payload),
            expected_digest=payload["artifact_digest"],
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=86_400,
        )


def test_upgrade_adds_only_the_horizon_and_recomputed_digest(tmp_path, monkeypatch):
    legacy = _payload()
    del legacy["preregistration"]["primary_horizon"]
    _redigest(legacy)
    original = json.loads(json.dumps(legacy))
    _manifest(
        tmp_path,
        [{"digest": legacy["artifact_digest"], "status": "superseded"}],
        monkeypatch,
    )

    upgraded, digest = upgrade_legacy_artifact_horizon(
        _write(tmp_path, legacy),
        expected_digest=legacy["artifact_digest"],
        minimum_seconds_to_post=600,
        maximum_seconds_to_post=86_400,
    )

    assert legacy == original
    assert upgraded["preregistration"] == {
        **legacy["preregistration"],
        "primary_horizon": {
            "minimum_seconds_to_post": 600,
            "maximum_seconds_to_post": 86_400,
        },
    }
    assert digest == upgraded["artifact_digest"]
    assert digest == compute_chaos_artifact_digest(upgraded)
    assert digest != legacy["artifact_digest"]
    loaded = load_chaos_artifact(
        _write(tmp_path, upgraded, name="upgraded.json"),
        approved_digests={digest},
        target_date=_TARGET_2026,
    )
    assert loaded.artifact_digest == digest


def test_approved_digest_permission_set_includes_superseded_entries(tmp_path, monkeypatch):
    active = "a" * 64
    superseded = "b" * 64
    _manifest(
        tmp_path,
        [
            {"digest": active, "status": "active"},
            {"digest": superseded, "status": "superseded"},
        ],
        monkeypatch,
    )

    assert approved_digests_from_manifest() == (active, superseded)


def test_current_digest_is_the_unique_active_entry_not_the_last_one(tmp_path, monkeypatch):
    active = "a" * 64
    superseded = "b" * 64
    _manifest(
        tmp_path,
        [
            {"digest": active, "status": "active"},
            {"digest": superseded, "status": "superseded"},
        ],
        monkeypatch,
    )

    assert resolve_current_digest() == active


@pytest.mark.parametrize(
    "entries",
    (
        [{"digest": "a" * 64, "status": "superseded"}],
        [
            {"digest": "a" * 64, "status": "active"},
            {"digest": "b" * 64, "status": "active"},
        ],
    ),
    ids=("no-active", "multiple-active"),
)
def test_current_digest_rejects_nonunique_active_entries(tmp_path, monkeypatch, entries):
    _manifest(tmp_path, entries, monkeypatch)

    with pytest.raises(ChaosArtifactManifestError, match="exactly one active"):
        resolve_current_digest()


def _difference_paths(before: object, after: object, *, prefix: str = "") -> set[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        paths: set[str] = set()
        for key in before.keys() | after.keys():
            path = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                paths.add(path)
            else:
                paths.update(_difference_paths(before[key], after[key], prefix=path))
        return paths
    return set() if before == after else {prefix}


def _readout_bytes(artifact) -> bytes:
    odds = {f"H{rank:02d}": 1.25 + rank for rank in range(1, 11)}
    ranks = {horse_id: rank for rank, horse_id in enumerate(odds, start=1)}
    raw, adjusted, band = chaos_readout(
        market_implied_win_probs(odds),
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=StageDiscount(
            lambda2=artifact.lambda2,
            lambda3=artifact.lambda3,
        ),
        edges=artifact.quintile_edges,
    )
    return json.dumps(
        {
            "band": band,
            "raw_masses": raw.event_mass,
            "adjusted_masses": adjusted.event_mass,
            "expected_s": adjusted.expected_s,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def test_issued_horizon_artifact_preserves_every_chaos_value(
    tmp_path,
    monkeypatch,
) -> None:
    """SC-010/INV-7: the committed issuance changes metadata at exactly two paths."""

    monkeypatch.delenv(chaos_artifact.CHAOS_APPROVED_MANIFEST_ENV, raising=False)
    previous_path = _ARTIFACT_DIR / f"{_PREVIOUS_DIGEST}.json"
    current_path = _ARTIFACT_DIR / f"{_CURRENT_DIGEST}.json"

    # The window-less side may only be read through the sanctioned bootstrap gate.
    upgraded_previous, upgraded_digest = upgrade_legacy_artifact_horizon(
        previous_path,
        expected_digest=_PREVIOUS_DIGEST,
        minimum_seconds_to_post=600,
        maximum_seconds_to_post=86_400,
    )
    previous_payload = copy.deepcopy(upgraded_previous)
    del previous_payload["preregistration"]["primary_horizon"]
    previous_payload["artifact_digest"] = _PREVIOUS_DIGEST

    # The comparison target is the independently committed file, not a second value
    # produced by upgrade_legacy_artifact_horizon.
    current_payload = json.loads(current_path.read_text(encoding="utf-8"))

    assert {
        field: previous_payload[field] for field in _INVARIANT_FIELDS
    } == {
        field: current_payload[field] for field in _INVARIANT_FIELDS
    }
    assert _difference_paths(previous_payload, current_payload) == {
        "artifact_digest",
        "preregistration.primary_horizon",
    }
    assert current_payload["preregistration"]["primary_horizon"] == {
        "basis": "schedule_jitter_floor_and_next_day_market_ceiling",
        "maximum_seconds_to_post": 86_400,
        "measured_coverage_of_pre_race_predict_clicks": 0.956,
        "minimum_seconds_to_post": 600,
    }

    # Create-only is checked against the filename of the file the sanctioned gate
    # actually read. Reconstructing the legacy payload is safe because that gate adds
    # exactly primary_horizon and its returned digest.
    assert previous_payload["artifact_digest"] == previous_path.stem
    assert compute_chaos_artifact_digest(previous_payload) == previous_path.stem
    assert compute_chaos_artifact_digest(current_payload) == current_path.stem

    upgraded_path = _write(
        tmp_path,
        upgraded_previous,
        name=f"{upgraded_digest}.json",
    )
    previous_artifact = load_chaos_artifact(
        upgraded_path,
        approved_digests={upgraded_digest},
        target_date=datetime.date(2026, 7, 26),
    )
    current_artifact = load_chaos_artifact(
        current_path,
        approved_digests={_CURRENT_DIGEST},
        target_date=datetime.date(2026, 7, 26),
    )

    assert _readout_bytes(previous_artifact) == _readout_bytes(current_artifact)
