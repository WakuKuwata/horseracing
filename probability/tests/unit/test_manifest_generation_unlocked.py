"""A manifest may belong to a generation other than lgbm-063 (feature 136).

Before this, ``calib_manifest`` stamped ``BASE_MODEL_VERSION = "lgbm-063"`` into every manifest
it built AND required that exact string when verifying one. Any manifest for a later generation
was therefore rejected by its own verifier, so the manifest calibration path could not be used
by the model that is actually ACTIVE (``mix-129-nj6``) -- the runtime path stayed in use by
default, with no out-of-sample material behind it.

The generation binding that matters is not "equals a constant from 2026": it is "this manifest
belongs to the model about to use it", and ``calib_activation.load_calibration`` has always
enforced exactly that against the ACTIVE model version. These tests pin that the redundant
constant is gone and the real binding is not.
"""
from __future__ import annotations

import datetime

import pytest

from horseracing_probability.calib_activation import ActivationError, Profile, load_calibration
from horseracing_probability.calib_manifest import ManifestError, verify_manifest
from tests.support.fixture_manifest import make_manifest, write_manifest_file

MIXTURE = "mix-129-nj6"


def test_a_mixture_generation_manifest_verifies():
    """The case that used to be impossible."""
    verify_manifest(make_manifest(base_model_version=MIXTURE))


def test_the_legacy_generation_still_verifies():
    verify_manifest(make_manifest(base_model_version="lgbm-063"))


def test_expected_generation_can_still_be_pinned_by_a_caller():
    manifest = make_manifest(base_model_version=MIXTURE)
    verify_manifest(manifest, expected_base_model_version=MIXTURE)
    with pytest.raises(ManifestError, match="generation mismatch"):
        verify_manifest(manifest, expected_base_model_version="lgbm-063")


@pytest.mark.parametrize("bad", ["", "   ", None, 123])
def test_a_manifest_without_a_real_generation_is_refused(bad):
    """Unlocking the constant must not let the field become meaningless."""
    manifest = make_manifest(base_model_version=MIXTURE)
    manifest["base_model_version"] = bad
    with pytest.raises(ManifestError):
        verify_manifest(manifest)


def test_activation_still_binds_the_manifest_to_the_active_model(tmp_path):
    """The real guard: a manifest for one generation cannot serve another.

    This is the kill-test for the change. If activation's binding were ever dropped along with
    the constant, a mixture manifest would silently calibrate a different model's probabilities.
    """
    path = write_manifest_file(tmp_path, make_manifest(base_model_version=MIXTURE))
    target = datetime.date(2025, 6, 1)
    with pytest.raises(ActivationError, match="generation mismatch"):
        load_calibration(path, active_model_version="lgbm-094-cap900",
                         target_date=target, profile=Profile.PRODUCTION)
    activation = load_calibration(path, active_model_version=MIXTURE,
                                  target_date=target, profile=Profile.PRODUCTION)
    assert activation.manifest_digest
