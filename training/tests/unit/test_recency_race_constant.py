"""Feature 101: 時間重みがレース内で一定であることの契約テスト。"""

from __future__ import annotations

import datetime

import numpy as np
import pytest
from horseracing_training.recency import RecencyWeightSpec, build_recency_weights

from horseracing_training.ev_weight import assert_race_constant


def _race_rows() -> tuple[np.ndarray, np.ndarray, datetime.date]:
    cutoff = datetime.date(2026, 2, 1)
    race_ids = np.array(["A", "A", "A", "B", "B", "C", "C", "C", "C"])
    race_dates = np.array(
        [cutoff] * 3
        + [cutoff - datetime.timedelta(days=90)] * 2
        + [cutoff - datetime.timedelta(days=730)] * 4
    )
    return race_ids, race_dates, cutoff


def test_rows_in_same_race_have_identical_weights() -> None:
    """同一レースの行を同じ係数にし、PL のレース単位尤度が壊れるのを防ぐ。"""
    race_ids, race_dates, cutoff = _race_rows()
    weights = build_recency_weights(
        race_ids,
        race_dates,
        cutoff=cutoff,
        spec=RecencyWeightSpec(half_life_days=365),
    )

    for race_id in np.unique(race_ids):
        race_weights = weights[race_ids == race_id]
        assert np.all(race_weights == race_weights[0])


def test_recency_weights_pass_existing_race_constant_guard() -> None:
    """既存の学習境界ガードを通し、時間重みを安全に配管できることを保証する。"""
    race_ids, race_dates, cutoff = _race_rows()
    weights = build_recency_weights(
        race_ids,
        race_dates,
        cutoff=cutoff,
        spec=RecencyWeightSpec(half_life_days=365),
    )

    assert_race_constant(race_ids, weights)


def test_per_horse_component_is_rejected_fail_closed() -> None:
    """馬ごとの項が混入した重みを拒否し、不正な listwise 尤度で学習する事故を防ぐ。"""
    race_ids, race_dates, cutoff = _race_rows()
    weights = build_recency_weights(
        race_ids,
        race_dates,
        cutoff=cutoff,
        spec=RecencyWeightSpec(half_life_days=365),
    )
    per_horse_component = np.linspace(0.0, 0.08, num=len(weights))

    with pytest.raises(ValueError, match="not constant within race"):
        assert_race_constant(race_ids, weights + per_horse_component)
