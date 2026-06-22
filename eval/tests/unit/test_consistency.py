"""US1 (SC-002): probability-consistency fail-fast."""

from __future__ import annotations

import pytest

from horseracing_eval.consistency import ConsistencyError, check_consistency
from horseracing_eval.predictor import Prediction


def _preds(d):
    return {k: Prediction(*v) for k, v in d.items()}


def test_valid_passes():
    # 2 horses: Σwin=1, Σtop2=2, Σtop3=target min(3,2)=2
    check_consistency(_preds({"a": (0.6, 1.0, 1.0), "b": (0.4, 1.0, 1.0)}))


def test_monotonicity_violation_fails():
    with pytest.raises(ConsistencyError):
        check_consistency(_preds({"a": (0.6, 0.5, 1.0), "b": (0.4, 1.0, 1.0)}))  # win>top2


def test_range_violation_fails():
    with pytest.raises(ConsistencyError):
        check_consistency(_preds({"a": (0.6, 1.0, 1.2), "b": (0.4, 1.0, 1.0)}))  # top3>1


def test_race_sum_violation_fails():
    with pytest.raises(ConsistencyError):
        check_consistency(_preds({"a": (0.9, 0.95, 1.0), "b": (0.6, 0.7, 0.8)}))  # Σwin=1.5
