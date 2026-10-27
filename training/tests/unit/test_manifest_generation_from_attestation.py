"""The manifest's generation comes from the attestation, not from a constant (feature 136).

``calib_manifest`` used to stamp a fixed ``"lgbm-063"`` into every manifest and then require
that exact string back when verifying one, so a manifest built for any later generation was
rejected by its own verifier. The generator now reads the generation from the attestation of
the recipe that produced the bundle, which is the only place that actually knows it.
"""
from __future__ import annotations

import pytest

from horseracing_training.oof_manifest import build_oof_manifest


def _att(**kw):
    return {"attestation_digest": "a" * 64, "base_model_version": "lgbm-063", **kw}


@pytest.mark.parametrize("value", [None, "", "   ", 123, {}])
def test_an_attestation_without_a_generation_is_refused(tmp_path, value):
    """Refuse rather than guess: a mislabelled generation is worse than no manifest.

    The failure must happen before any gate is fitted, so the error is about provenance and not
    about whatever the fitting happened to produce.
    """
    attestation = _att()
    if value is None:
        attestation.pop("base_model_version")
    else:
        attestation["base_model_version"] = value
    with pytest.raises(ValueError, match="base_model_version"):
        build_oof_manifest(None, {"bundle_digest": "b" * 64}, attestation=attestation,
                           code_sha="deadbeef", out_root=tmp_path)


def test_attestation_bundle_mismatch_still_reports_itself_first(tmp_path):
    """The generation check must not mask the more specific provenance error.

    A bundle generated from a different attestation is a sharper diagnosis than a missing
    generation field, so it has to surface first even when both are wrong.
    """
    attestation = _att()
    attestation.pop("base_model_version")
    bundle = {"bundle_digest": "b" * 64, "attestation_digest": "c" * 64}
    with pytest.raises(ValueError, match="attestation mismatch"):
        build_oof_manifest(None, bundle, attestation=attestation,
                           code_sha="deadbeef", out_root=tmp_path)
