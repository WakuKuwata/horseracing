"""Feature 097: an arm-E predictor that receives a SHARED matrix must still honour drop_features.

Found by the 097 gate: candidate (all columns) vs active (drop=early_mid_pace) came out with a
paired diff of exactly 0.000000 over 10,366 races — the shared matrix was injected raw into the
base predictor, bypassing the scope that ``_ensure_data`` applies. Two identical models were
compared and the run nearly recorded a REJECT. This pins both paths to one scope.
"""

from __future__ import annotations

import pandas as pd

from horseracing_training.calib_split import OofCalibratedPredictor
from horseracing_training.dataset import TrainingMatrix
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.recipe import ModelRecipe


def _matrix():
    cols = ["a", "b", "c", "asof_rel_early_mid_avg", "asof_rel_early_mid_best"]
    frame = pd.DataFrame({"race_id": ["r"], "horse_id": ["h"], **{c: [0.0] for c in cols},
                          "race_date": [pd.Timestamp("2020-01-01")], "win": [1]})
    return TrainingMatrix(frame=frame, feature_cols=cols, categorical_cols=[])


DROP = ("asof_rel_early_mid_avg", "asof_rel_early_mid_best")


def test_scope_drops_columns_on_built_matrix():
    p = LightGBMPredictor(session=None, drop_features=DROP)
    scoped = p._scope_columns(_matrix())
    assert scoped.feature_cols == ["a", "b", "c"]


def test_scope_without_drop_is_identity():
    p = LightGBMPredictor(session=None)
    m = _matrix()
    assert p._scope_columns(m) is m


def test_injected_shared_matrix_is_scoped_for_the_drop_arm():
    """The bug: shared_data assigned raw -> drop ignored -> arm identical to candidate."""
    shared = _matrix()
    dropped = OofCalibratedPredictor(
        None, ModelRecipe(objective="pl_topk", calibration="none", drop_features=DROP),
        shared_data=shared, n_oof_blocks=2, method="isotonic")
    full = OofCalibratedPredictor(
        None, ModelRecipe(objective="pl_topk", calibration="none"),
        shared_data=shared, n_oof_blocks=2, method="isotonic")
    pd_ = dropped._make_base()
    pf_ = full._make_base()
    assert set(pf_._data.feature_cols) - set(pd_._data.feature_cols) == set(DROP)
    assert pd_._data.frame is shared.frame          # frame shared, never copied or mutated
    assert shared.feature_cols[-1] == DROP[-1]       # the shared object itself is untouched
