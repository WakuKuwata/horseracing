"""Feature 108 (T009): arm E attestation records the truth, and every shortcut fails closed.

Before this feature `build_attestation` filled a missing `calib_frac` with the legacy default
and a null `calibration_split_unit` with the legacy split unit, so an arm E model — which holds
nothing out and uses no internal split — was attested as a 70/30 legacy model, with its OOF
block count and weight mask dropped on the floor. Nothing raised. These tests pin both halves of
the fix: the recorded facts are the real ones, and every way of half-recording them is a typed
error rather than a default.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from horseracing_training.legacy_attest import (
    ARM_E_CALIBRATION_METHOD,
    AttestationError,
    attestation_from_model_dir,
    recipe_from_attestation,
)
from horseracing_training.legacy_attest import _validate_payload as validate_payload

_REPO = Path(__file__).resolve().parents[3]
_ARM_E_DIR = _REPO / "artifacts" / "model_versions" / "lgbm-094-cap900"
_needs_arm_e = pytest.mark.skipif(not _ARM_E_DIR.exists(), reason="arm E artifacts absent")


def _payload() -> dict:
    att = attestation_from_model_dir(_ARM_E_DIR, code_sha="t")
    return {k: v for k, v in att.items() if k != "attestation_digest"}


def _expect_error(payload: dict, *, match: str | None = None):
    with pytest.raises(AttestationError, match=match):
        validate_payload(payload, enforce_legacy=False)


@_needs_arm_e
def test_arm_e_records_the_construction_not_a_legacy_fiction():
    p = _payload()
    internal = p["internal_calibration"]
    assert internal["method"] == ARM_E_CALIBRATION_METHOD
    # the two fabricated values are gone
    assert internal["calibration_split_unit"] is None
    assert internal["calib_frac"] == {"requested": None, "effective": 0.0}
    # and the facts that used to be dropped are present
    assert internal["n_oof_blocks"] == 8
    assert internal["protocol_version"] == "strict_past_oof_isotonic_v1"
    assert p["weight_mask"] == {"rate": 0.5, "seed": 20260810}
    assert p["resolved_lgbm_params"]["n_estimators"] == 900


@_needs_arm_e
def test_no_field_is_silently_defaulted():
    """FR-005a: the shipping view must be the metadata's own values, not filled-in ones."""
    from horseracing_training.calibration import (
        DEFAULT_CALIB_FRAC,
        LEGACY_CALIBRATION_SPLIT_UNIT,
    )

    internal = _payload()["internal_calibration"]
    assert internal["calib_frac"]["requested"] != DEFAULT_CALIB_FRAC
    assert internal["calibration_split_unit"] != LEGACY_CALIBRATION_SPLIT_UNIT


@_needs_arm_e
@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda i, p: i.pop("n_oof_blocks"), "missing block count"),
        (lambda i, p: i.__setitem__("n_oof_blocks", 0), "non-positive block count"),
        (lambda i, p: i.__setitem__("calibration_split_unit", "race_count_v1"),
         "a split unit would read as a 70/30 model"),
        (lambda i, p: i.__setitem__("calib_frac", 0.3), "scalar calib_frac loses requested/effective"),
        (lambda i, p: i["calib_frac"].__setitem__("effective", 0.3), "booster held nothing out"),
        (lambda i, p: i.pop("protocol_version"), "missing protocol version"),
        (lambda i, p: i.__setitem__("protocol_version", "strict_past_oof_isotonic_v2"),
         "unknown protocol version"),
        (lambda i, p: i.__setitem__("method", "isotonic_oof_typo"), "unknown method"),
        (lambda i, p: p.__setitem__("weight_mask", {"rate": 0.5}), "half a mask"),
        (lambda i, p: p.__setitem__("weight_mask", {"rate": 1.5, "seed": 1}), "rate out of range"),
    ],
)
def test_every_half_recorded_shape_is_rejected(mutate, why):
    p = _payload()
    mutate(p["internal_calibration"], p)
    _expect_error(p)


@_needs_arm_e
def test_an_unknown_method_does_not_fall_through_to_legacy_rules():
    """The vocabulary is closed: 'not arm E' must not mean 'therefore legacy' (085's lesson)."""
    p = _payload()
    p["internal_calibration"]["method"] = "something_new"
    _expect_error(p, match="unknown calibration method")


@_needs_arm_e
def test_legacy_payload_may_not_carry_arm_e_keys():
    """Cross-consistency (FR-005c): the shipping view and the construction must agree."""
    p = _payload()
    p["internal_calibration"] = {
        "method": "isotonic", "calib_frac": 0.3, "calibration_split_unit": "race_count_v1",
    }
    _expect_error(p, match="weight_mask is only meaningful")


@_needs_arm_e
def test_legacy_rules_still_reject_a_zero_holdout_claiming_to_be_legacy():
    p = _payload()
    p.pop("weight_mask", None)
    p["internal_calibration"] = {
        "method": "isotonic", "calib_frac": 0.0, "calibration_split_unit": "race_count_v1",
    }
    _expect_error(p, match="between zero and one")


@_needs_arm_e
def test_reconstructed_recipe_uses_recorded_values_not_code_defaults():
    """`n_estimators` default is 300 and the live model is 900 — a default here ships a
    different model under the attested name."""
    att = attestation_from_model_dir(_ARM_E_DIR, code_sha="t")
    recipe = recipe_from_attestation_general(att)
    assert dict(recipe.params or ())["n_estimators"] == 900
    assert (recipe.weight_mask_rate, recipe.weight_mask_seed) == (0.5, 20260810)
    # the recipe arm E was really built from declares ORDINARY isotonic settings; the OOF-ness
    # lives in the wrapping predictor, so a recipe built from the shipping view would not even
    # be constructible (split_unit=None raises).
    assert recipe.calibration == "isotonic"
    assert recipe.calibration_split_unit is not None


def recipe_from_attestation_general(att: dict):
    """`recipe_from_attestation` enforces the lgbm-063 pin; arm E goes through the general path."""
    from horseracing_training.legacy_attest import _recipe_from_payload, _validated_payload

    return _recipe_from_payload(_validated_payload(att, enforce_legacy=False))


@_needs_arm_e
def test_the_legacy_pinned_entry_still_refuses_arm_e():
    att = attestation_from_model_dir(_ARM_E_DIR, code_sha="t")
    with pytest.raises(AttestationError, match="legacy base_model_version"):
        recipe_from_attestation(copy.deepcopy(att))
