"""Feature 074 D9 (T032): restrict_features reproduces a legacy model's exact columns.

Proves the fit can be pinned to an ordered subset of the current feature schema (inclusion), which
is how OOF regeneration reproduces lgbm-063 (features-017) on the current features-018 schema.
Byte-parity of the shared column VALUES is guaranteed by 069's additive-merge parity; here we
verify the column-restriction mechanism (exact ordered set, fail-closed on a missing column, and
restrict=None left byte-unchanged).
"""

from __future__ import annotations

import pytest

from horseracing_training.predictor import LightGBMPredictor
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration


def _fit(session, restrict):
    p = LightGBMPredictor(session, objective="binary", calibration="none",
                          target_encode_cols=(), restrict_features=restrict)
    from horseracing_eval.dataset import load_eval_races
    races = load_eval_races(session)
    p.fit([er.context for er in races])
    return p


def test_restrict_features_keeps_exactly_the_ordered_subset(session):
    seed_learnable(session, years=(2007, 2008), races_per_year=8, field_size=6)
    full = _fit(session, None)
    full_cols = list(full.feature_cols_)
    assert len(full_cols) > 3

    # restrict to the first 3 columns in a chosen order; the fit must use exactly those, in order.
    wanted = tuple(full_cols[:3])
    restricted = _fit(session, wanted)
    assert list(restricted.feature_cols_) == list(wanted)


def test_restrict_features_fail_closed_on_missing_column(session):
    seed_learnable(session, years=(2007, 2008), races_per_year=6, field_size=6)
    with pytest.raises(ValueError, match="restrict_features not present"):
        _fit(session, ("__no_such_feature_column__",))


def test_restrict_none_uses_full_schema(session):
    seed_learnable(session, years=(2007, 2008), races_per_year=6, field_size=6)
    from horseracing_training.dataset import model_input_features
    p = _fit(session, None)
    assert list(p.feature_cols_) == list(model_input_features())
