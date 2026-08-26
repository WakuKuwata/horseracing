"""Feature 101: 行数を基底にした時間重み正規化の契約テスト。"""

from __future__ import annotations

import datetime

import numpy as np
import pytest
from horseracing_training.recency import RecencyWeightSpec, build_recency_weights


def _uneven_field_rows() -> tuple[np.ndarray, np.ndarray, datetime.date]:
    cutoff = datetime.date(2026, 1, 1)
    field_sizes = (5, 18, 8)
    ages = (0, 365, 1460)
    race_ids = np.concatenate(
        [np.repeat(race_id, size) for race_id, size in zip("ABC", field_sizes, strict=True)]
    )
    race_dates = np.concatenate(
        [
            np.repeat(cutoff - datetime.timedelta(days=age), size)
            for age, size in zip(ages, field_sizes, strict=True)
        ]
    )
    return race_ids, race_dates, cutoff


def _raw_alpha_tilde(
    race_dates: np.ndarray,
    *,
    cutoff: datetime.date,
    spec: RecencyWeightSpec,
) -> np.ndarray:
    ages = np.array([(cutoff - race_date).days for race_date in race_dates], dtype=float)
    return spec.floor + (1.0 - spec.floor) * 0.5 ** (ages / spec.half_life_days)


def _assert_row_sum_equals_n(weights: np.ndarray) -> None:
    assert np.isclose(float(np.sum(weights)), len(weights), rtol=1e-9, atol=0.0)


def test_weights_sum_to_number_of_rows() -> None:
    """行重み総和を行数に保ち、LightGBM の実効的な正則化量の変動を防ぐ。"""
    race_ids, race_dates, cutoff = _uneven_field_rows()

    weights = build_recency_weights(
        race_ids,
        race_dates,
        cutoff=cutoff,
        spec=RecencyWeightSpec(half_life_days=365),
    )

    _assert_row_sum_equals_n(weights)


def test_unnormalized_raw_weights_fail_row_sum_contract() -> None:
    """正規化を外す変異が総重みを減らし、時間減衰と学習容量を混同するのを検出する。"""
    race_ids, race_dates, cutoff = _uneven_field_rows()
    spec = RecencyWeightSpec(half_life_days=365)
    raw_weights = _raw_alpha_tilde(race_dates, cutoff=cutoff, spec=spec)

    with pytest.raises(AssertionError):
        _assert_row_sum_equals_n(raw_weights)


def test_race_mean_normalization_fails_row_sum_contract() -> None:
    """頭数差があるとレース平均1では行総量がずれるため、誤った正規化への変異を阻止する。"""
    race_ids, race_dates, cutoff = _uneven_field_rows()
    spec = RecencyWeightSpec(half_life_days=365)
    raw_weights = _raw_alpha_tilde(race_dates, cutoff=cutoff, spec=spec)
    per_race_raw = np.array(
        [raw_weights[race_ids == race_id][0] for race_id in np.unique(race_ids)]
    )
    race_mean_normalized = raw_weights / float(np.mean(per_race_raw))

    assert np.isclose(
        float(
            np.mean(
                [
                    race_mean_normalized[race_ids == race_id][0]
                    for race_id in np.unique(race_ids)
                ]
            )
        ),
        1.0,
    )
    with pytest.raises(AssertionError):
        _assert_row_sum_equals_n(race_mean_normalized)

    correct = build_recency_weights(race_ids, race_dates, cutoff=cutoff, spec=spec)
    assert not np.allclose(race_mean_normalized, correct)


@pytest.mark.parametrize("half_life_days", [30, 365, 7300])
def test_row_sum_is_preserved_for_every_valid_half_life(half_life_days: float) -> None:
    """半減期を変更しても総重みを行数に固定し、候補間の学習容量を比較可能に保つ。"""
    race_ids, race_dates, cutoff = _uneven_field_rows()

    weights = build_recency_weights(
        race_ids,
        race_dates,
        cutoff=cutoff,
        spec=RecencyWeightSpec(half_life_days=half_life_days),
    )

    _assert_row_sum_equals_n(weights)
