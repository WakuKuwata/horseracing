"""Feature 091 T061/T067: the weight-regime marker and its observability.

The marker exists to FILTER later ("was this predicted before the weights were published?"), not
to audit — feature_snapshots already stores the per-horse input vector. Two things must hold:
its value must match the input the model actually received, and it must take part in the
idempotency key, or the better-informed full-info prediction can never be produced (research D6,
the same trap 076's ';calib=' fell into).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from horseracing_serving.pipeline import WeightRegimeObserver, _wregime_lv, _wregime_of
from horseracing_serving.predictor import race_weight_availability


class _Model:
    def __init__(self, *, has_prev: bool = True):
        self.feature_cols = ["weight", "weight_diff", "carried_weight_ratio", "age"]
        if has_prev:
            self.feature_cols.append("prev_weight")


def _race(weights: list[float | None], race_id: str = "R1") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "race_id": race_id,
            "horse_id": [f"H{i}" for i in range(len(weights))],
            "weight": [np.nan if w is None else w for w in weights],
            "weight_diff": 2.0,
            "carried_weight_ratio": 0.12,
            "prev_weight": 460.0,
            "age": 4,
        }
    )


def _avail(weights, *, has_prev=True):
    return race_weight_availability(_race(weights), "R1", model=_Model(has_prev=has_prev))


def test_marker_reflects_what_the_model_received():
    assert _wregime_of(_avail([460.0, 462.0, 458.0])) == "full_info"
    assert _wregime_of(_avail([None, None, None])) == "serving"
    # mixed: the rule strips today's weight from the WHOLE race, so the model got none of it
    assert _wregime_of(_avail([460.0, None, 458.0])) == "serving"


def test_mixed_race_marker_agrees_with_the_frame_the_model_gets():
    """The marker would be worse than useless if it disagreed with the actual input."""
    a = _avail([460.0, None, 458.0])
    assert a.normalised
    assert pd.to_numeric(a.rows["weight"], errors="coerce").isna().all()
    assert _wregime_of(a) == "serving"


def test_model_without_prev_weight_gets_no_marker():
    """No regime distinction exists for it, and adding a segment would change its logic_version —
    and therefore its idempotency key — for no reason."""
    a = _avail([460.0, None], has_prev=False)
    assert _wregime_of(a) is None
    assert _wregime_lv("feat=x;serve=y", a) == "feat=x;serve=y"


def test_marker_is_appended_not_substituted():
    lv = _wregime_lv("feat=features-021;serve=serve-0.1.0;sdisc=harville", _avail([None]))
    assert lv == "feat=features-021;serve=serve-0.1.0;sdisc=harville;wregime=serving"


# --- T067 observability -------------------------------------------------------------------------


def test_observer_separates_given_up_full_info_from_never_had_it():
    obs = WeightRegimeObserver()
    obs.observe(_avail([460.0, 461.0]))          # full info
    obs.observe(_avail([None, None]))            # never weighed
    obs.observe(_avail([460.0, None, 462.0]))    # mixed -> collapsed
    d = obs.as_dict()
    assert d["races_regime_applicable"] == 3
    assert d["races_full_info"] == 1
    assert d["races_serving_regime"] == 2
    assert d["races_normalised"] == 1  # only the mixed one had anything to give up
    assert d["post_normalisation_uniformity_violations"] == 0
    assert sum(d["weighed_fraction_of_normalised_races"].values()) == 1


def test_observer_ignores_models_the_rule_cannot_apply_to():
    obs = WeightRegimeObserver()
    obs.observe(_avail([460.0, None], has_prev=False))
    assert obs.as_dict()["races_regime_applicable"] == 0


def test_uniformity_violation_is_detected():
    """The health check must be able to fail: a frame that stayed mixed after normalisation means
    the FR-034 rule did not take effect and every horse's probability is off."""
    from horseracing_serving.predictor import WeightAvailability

    broken = WeightAvailability(
        rows=_race([460.0, None, 458.0]), applicable=True, normalised=True,
        n_started=3, n_weighed=2,
    )
    obs = WeightRegimeObserver()
    obs.observe(broken)
    assert obs.as_dict()["post_normalisation_uniformity_violations"] == 1
