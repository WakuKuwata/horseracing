"""Feature 101: 時間減衰曲線の形状と fold 間の意味を固定するテスト。"""

from __future__ import annotations

import datetime

import numpy as np

from horseracing_training.recency import RecencyWeightSpec, build_recency_weights


def _raw_alpha_tilde(age_days: int, spec: RecencyWeightSpec) -> float:
    return spec.floor + (1.0 - spec.floor) * 0.5 ** (
        age_days / spec.half_life_days
    )


def test_weight_is_nonincreasing_as_age_grows() -> None:
    """古いレースほど重みが大きくなる逆転を検出し、recency の意味を守る。"""
    cutoff = datetime.date(2026, 5, 1)
    ages = (0, 30, 365, 3650)
    race_dates = [cutoff - datetime.timedelta(days=age) for age in ages]

    weights = build_recency_weights(
        ["A", "B", "C", "D"],
        race_dates,
        cutoff=cutoff,
        spec=RecencyWeightSpec(half_life_days=365),
    )

    assert np.all(np.diff(weights) <= 0.0)


def test_races_on_same_date_have_same_weight() -> None:
    """同じ age のレースを同値にし、race_id など日付以外の情報が混ざるのを防ぐ。"""
    cutoff = datetime.date(2026, 5, 1)
    shared_date = cutoff - datetime.timedelta(days=240)
    race_dates = [shared_date, cutoff, shared_date]

    weights = build_recency_weights(
        ["A", "B", "C"],
        race_dates,
        cutoff=cutoff,
        spec=RecencyWeightSpec(half_life_days=365),
    )

    assert weights[0] == weights[2]


def test_positive_floor_keeps_ancient_weight_above_zero() -> None:
    """指数部がアンダーフローするほど古い行も残し、学習母集団からの消失を防ぐ。"""
    cutoff = datetime.date(2026, 5, 1)
    weights = build_recency_weights(
        ["ancient", "recent"],
        [datetime.date.min, cutoff],
        cutoff=cutoff,
        spec=RecencyWeightSpec(half_life_days=30, floor=0.05),
    )

    assert weights[0] > 0.0


def test_same_age_has_same_raw_weight_across_folds() -> None:
    """fold ごとの母集団で正規化倍率は変わっても、同じ age の生の α̃ の意味を固定する。"""
    spec = RecencyWeightSpec(half_life_days=365, floor=0.05)
    target_age = 180
    cutoff_a = datetime.date(2024, 12, 31)
    cutoff_b = datetime.date(2026, 12, 31)
    ages_a = (target_age, 0)
    ages_b = (target_age, 1095)
    dates_a = [cutoff_a - datetime.timedelta(days=age) for age in ages_a]
    dates_b = [cutoff_b - datetime.timedelta(days=age) for age in ages_b]

    weights_a = build_recency_weights(
        ["target", "peer"], dates_a, cutoff=cutoff_a, spec=spec
    )
    weights_b = build_recency_weights(
        ["target", "peer"], dates_b, cutoff=cutoff_b, spec=spec
    )
    raw_a = np.array([_raw_alpha_tilde(age, spec) for age in ages_a])
    raw_b = np.array([_raw_alpha_tilde(age, spec) for age in ages_b])

    # 生の α̃ は cutoff に依らず age だけで決まる = fold 間で「重みの意味」が変わらない
    assert raw_a[0] == raw_b[0]
    # 正規化後は母集団が違うのでスケールが変わりうる。期待値は**行重み総和 = 行数**から作る
    # (レース平均 1 で書くと、1 レース 1 行のときだけ偶然一致して契約違反を見逃す)。
    np.testing.assert_allclose(weights_a, raw_a * len(raw_a) / raw_a.sum(), rtol=1e-12)
    np.testing.assert_allclose(weights_b, raw_b * len(raw_b) / raw_b.sum(), rtol=1e-12)
    assert not np.isclose(weights_a[0], weights_b[0])
