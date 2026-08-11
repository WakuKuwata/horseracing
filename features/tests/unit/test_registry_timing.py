"""Regression tests for registry availability timing.

The ``carried_weight_ratio`` correction is independent of the weight-imputation feature's
adoption or rejection and must survive either verdict's rollback.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from horseracing_features.registry import REGISTRY, AvailabilityTiming, model_input_features

_T = AvailabilityTiming
_TIMING_ORDER = {
    _T.PRE_ENTRY: 0,
    _T.POST_FRAME: 1,
    _T.POST_WEIGHT: 2,
    _T.POST_ODDS: 3,
    _T.PRE_RACE: 4,
    _T.POST_RESULT: 5,
}

# Latest input timings established by auditing static_features.py and lowcost_features.py.
# All registry columns not listed here are either direct PRE_ENTRY inputs or are derived strictly
# from past/as-of inputs. Audit result: there are no additional timing mismatches.
_NON_DEFAULT_INPUT_DEPENDENCIES = {
    "frame": {"frame": _T.POST_FRAME},
    "horse_number": {"horse_number": _T.POST_FRAME},
    "weight": {"weight": _T.POST_WEIGHT},
    "weight_diff": {"weight_cell": _T.POST_WEIGHT},
    "carried_weight": {"jockey_weight": _T.PRE_ENTRY},
    "carried_weight_ratio": {
        "jockey_weight": _T.PRE_ENTRY,
        "weight": _T.POST_WEIGHT,
    },
    "carried_weight_rel": {
        "jockey_weight": _T.PRE_ENTRY,
        "started_entries": _T.PRE_ENTRY,
    },
    "carried_weight_change": {
        "jockey_weight": _T.PRE_ENTRY,
        "prior_jockey_weight": _T.PRE_ENTRY,
    },
}


def _feature_hash(feature_cols: list[str]) -> str:
    """Mirror horseracing_training.artifacts.feature_hash without a cross-package dependency."""
    return hashlib.sha256("|".join(feature_cols).encode()).hexdigest()


def test_carried_weight_ratio_is_post_weight():
    assert REGISTRY["carried_weight_ratio"].timing == _T.POST_WEIGHT


def test_timing_correction_does_not_change_feature_hash(monkeypatch):
    """The metadata correction is adoption-independent and must not change model inputs."""
    corrected_features = model_input_features()
    corrected_hash = _feature_hash(corrected_features)

    old_meta = REGISTRY["carried_weight_ratio"]
    monkeypatch.setitem(REGISTRY, "carried_weight_ratio", replace(old_meta, timing=_T.PRE_ENTRY))
    before_correction_features = model_input_features()

    assert before_correction_features == corrected_features
    # Compare BEFORE vs AFTER the correction, not against a pinned constant. The invariant is
    # "flipping availability_timing is hash-neutral", which must keep holding as columns are added
    # or removed by other features. Pinning an absolute hash here would silently also assert "the
    # column set never changes" and would fail the moment any feature adds a column (Feature 091
    # adding `prev_weight` is exactly that case).
    assert _feature_hash(before_correction_features) == corrected_hash


def test_no_feature_depends_on_input_later_than_declared():
    """Audit every registry column against the latest timing of its implementation inputs."""
    assert set(_NON_DEFAULT_INPUT_DEPENDENCIES) <= set(REGISTRY)

    violations = []
    for feature, meta in REGISTRY.items():
        dependencies = _NON_DEFAULT_INPUT_DEPENDENCIES.get(
            feature, {"direct_or_strictly_before_input": _T.PRE_ENTRY}
        )
        latest_input = max(dependencies.values(), key=_TIMING_ORDER.__getitem__)
        if _TIMING_ORDER[meta.timing] < _TIMING_ORDER[latest_input]:
            violations.append(
                f"{feature}: declared={meta.timing.value}, latest_input={latest_input.value}, "
                f"dependencies={dependencies}"
            )

    assert not violations, "availability_timing violations:\n" + "\n".join(violations)
