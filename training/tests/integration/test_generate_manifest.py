"""Feature 078 US2 (T012): build_oof_manifest — the first production build_manifest caller.

Generates a REAL v3 manifest from an OOF bundle: verdicts from the pre-registered OOF gates,
deployment params gated on those verdicts, published create-only keyed by manifest_digest (so a
re-evaluation of the same bundle never conflicts). Deterministic; a non-eligible manifest is
fail-closed at the production loader.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_probability.calib_manifest import SCHEMA_VERSION_V3, verify_manifest

from horseracing_training.oof_manifest import build_oof_manifest
from tests._synth import insert_race

pytestmark = pytest.mark.integration

_ATT = {"attestation_digest": "a" * 64}
_GATE = {"verdict": {"non_inferior_margin_ece": 0.001, "no_decision_min_days": 10},
         "transfer_check": {"ks_distance_max": 0.10}}


def _seed_and_bundle(session):
    """A few races across 3 years → the OOF gates run but the thin fixture yields NO_DECISION."""
    preds = {}
    for year in (2007, 2008, 2009):
        for r in range(1, 4):
            rid = f"{year}06{r:02d}0101"
            insert_race(session, race_id=rid, race_date=datetime.date(year, 6, r), horses=[
                {"horse_id": "H1", "horse_number": 1, "finish_order": 1},
                {"horse_id": "H2", "horse_number": 2, "finish_order": 2},
                {"horse_id": "H3", "horse_number": 3, "finish_order": 3},
            ])
            preds[rid] = {
                "H1": {"win": 0.6, "top2": 0.82, "top3": 0.93},
                "H2": {"win": 0.25, "top2": 0.55, "top3": 0.75},
                "H3": {"win": 0.15, "top2": 0.33, "top3": 0.52},
            }
    return {"predictions": preds, "bundle_digest": "b" * 64}


def test_attestation_mismatch_is_refused(session, tmp_path):
    """codex D7 (surfaced by the T015 real run): a bundle generated from a DIFFERENT recipe
    attestation than the supplied one is refused — the manifest can't mislabel provenance."""
    bundle = _seed_and_bundle(session)
    bundle["attestation_digest"] = "c" * 64  # bundle was generated from a different generation
    with pytest.raises(ValueError, match="attestation mismatch"):
        build_oof_manifest(session, bundle, attestation=_ATT, code_sha="078",
                           out_root=tmp_path, gate_config=_GATE, min_races=2)


def test_generates_v3_manifest_at_digest_keyed_path(session, tmp_path):
    bundle = _seed_and_bundle(session)
    path, manifest = build_oof_manifest(
        session, bundle, attestation=_ATT, code_sha="078", out_root=tmp_path,
        gate_config=_GATE, min_races=2)
    assert manifest["schema_version"] == SCHEMA_VERSION_V3
    verify_manifest(manifest)  # a well-formed, self-consistent v3 artifact
    # published under artifacts/oof/<bundle>/manifests/<manifest_digest>/manifest.json (T010)
    assert path.parent.name == manifest["manifest_digest"]
    assert path.parent.parent.name == "manifests"
    assert path.parent.parent.parent.name == "b" * 64


def test_deterministic_manifest_digest(session, tmp_path):
    bundle = _seed_and_bundle(session)
    _p1, m1 = build_oof_manifest(session, bundle, attestation=_ATT, code_sha="078",
                                 out_root=tmp_path, gate_config=_GATE, min_races=2)
    # re-run into a fresh root: identical inputs → identical manifest_digest (determinism, D7)
    _p2, m2 = build_oof_manifest(session, bundle, attestation=_ATT, code_sha="078",
                                 out_root=tmp_path / "again", gate_config=_GATE, min_races=2)
    assert m1["manifest_digest"] == m2["manifest_digest"]


def test_republish_same_manifest_is_idempotent(session, tmp_path):
    bundle = _seed_and_bundle(session)
    p1, _ = build_oof_manifest(session, bundle, attestation=_ATT, code_sha="078",
                               out_root=tmp_path, gate_config=_GATE, min_races=2)
    p2, _ = build_oof_manifest(session, bundle, attestation=_ATT, code_sha="078",
                               out_root=tmp_path, gate_config=_GATE, min_races=2)
    assert p1 == p2  # create-only, identical content → idempotent success (no conflict)


def test_thin_fixture_is_not_eligible_and_loader_refuses(session, tmp_path):
    from horseracing_probability.calib_activation import (
        ActivationError,
        Profile,
        load_calibration,
    )
    bundle = _seed_and_bundle(session)
    path, manifest = build_oof_manifest(
        session, bundle, attestation=_ATT, code_sha="078", out_root=tmp_path,
        gate_config=_GATE, min_races=2)
    # the thin fixture cannot reach ADOPT on both stages → not eligible (D6)
    assert manifest["activation_eligible"] is False
    with pytest.raises(ActivationError, match="not production-eligible"):
        load_calibration(str(path), active_model_version="lgbm-063",
                         target_date=datetime.date(2025, 6, 1), profile=Profile.PRODUCTION)
