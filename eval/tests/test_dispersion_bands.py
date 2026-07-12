"""Feature 066 dispersion band numerics (pure): entropy / raw facts / frozen quintile edges.

Includes the field-size robustness case (T010): normalised entropy is comparable across a small and
a large field, and quintile edges come only from the predictor distribution (no outcome input).
"""

from __future__ import annotations

import math

import pytest

from horseracing_eval.dispersion_bands import (
    DELTA_EPS,
    DispersionPCalibrator,
    assign_band,
    entropy_delta_direction,
    favorite_win_prob,
    fit_quintile_edges,
    normalized_entropy,
    top3_cumulative,
)


def test_uniform_field_entropy_is_one():
    assert normalized_entropy([0.25, 0.25, 0.25, 0.25]) == pytest.approx(1.0)


def test_concentrated_field_low_entropy():
    h = normalized_entropy([0.9, 0.05, 0.03, 0.02])
    assert h is not None and h < 0.5


def test_entropy_none_below_two():
    assert normalized_entropy([1.0]) is None
    assert normalized_entropy([]) is None


def test_normalized_entropy_comparable_small_vs_large_field():
    # Uniform 5-horse and uniform 16-horse fields both = 1.0 despite very different max(q):
    # normalisation by ln N makes the concentration measure field-size comparable (T010).
    small = normalized_entropy([1 / 5] * 5)
    large = normalized_entropy([1 / 16] * 16)
    assert small == pytest.approx(1.0) and large == pytest.approx(1.0)
    # max(q) is NOT comparable (0.2 vs 0.0625) — that's why the band uses entropy, not max(q).
    assert favorite_win_prob([1 / 5] * 5) == pytest.approx(0.2)
    assert favorite_win_prob([1 / 16] * 16) == pytest.approx(0.0625)


def test_top3_cumulative():
    assert top3_cumulative([0.4, 0.3, 0.2, 0.1]) == pytest.approx(0.9)


def test_fit_quintile_edges_deterministic_and_from_predictor_only():
    entropies = [i / 100 for i in range(1, 101)]  # 0.01..1.00
    edges = fit_quintile_edges(entropies)
    assert len(edges) == 4
    assert edges == sorted(edges)
    # edges are quantiles of the entropy distribution only — no results consulted.
    assert edges[0] == pytest.approx(0.208, abs=0.02)


def test_fit_quintile_edges_needs_five_samples():
    with pytest.raises(ValueError):
        fit_quintile_edges([0.1, 0.2, 0.3, 0.4])


def test_assign_band_orders_low_entropy_firm():
    edges = [0.2, 0.4, 0.6, 0.8]
    assert assign_band(0.1, edges) == "firm"
    assert assign_band(0.5, edges) == "standard"
    assert assign_band(0.99, edges) == "open"
    assert assign_band(None, edges) is None
    assert assign_band(0.5, None) is None  # F8: no boundary -> no band


def test_entropy_matches_manual_formula():
    q = [0.5, 0.3, 0.2]
    manual = -sum(v * math.log(v) for v in q) / math.log(3)
    assert normalized_entropy(q) == pytest.approx(manual)


# --- Feature 066 model_delta numerics -----------------------------------------


def test_entropy_delta_direction_model_more_open():
    delta, direction = entropy_delta_direction(0.95, 0.80)
    assert delta == pytest.approx(0.15)
    assert direction == "model_more_open"


def test_entropy_delta_direction_model_more_firm():
    delta, direction = entropy_delta_direction(0.60, 0.90)
    assert delta == pytest.approx(-0.30)
    assert direction == "model_more_firm"


def test_entropy_delta_direction_similar_within_eps():
    # |ΔH| within the pre-registered dead-band → similar (no false disagreement).
    delta, direction = entropy_delta_direction(0.80 + DELTA_EPS / 2, 0.80)
    assert direction == "similar"
    assert abs(delta) <= DELTA_EPS


def test_entropy_delta_direction_none_when_degenerate():
    assert entropy_delta_direction(None, 0.8) == (None, None)
    assert entropy_delta_direction(0.8, None) == (None, None)


def test_dispersion_pcalibrator_json_roundtrip():
    art = DispersionPCalibrator(
        method="two_gamma", gamma_lo=1.63, gamma_hi=0.47, pivot=0.15,
        fit_from="2024-01-01", fit_to="2024-12-31", as_of="2024-12-31",
        version="pcal-v1", n_races=3200,
    )
    import json

    data = json.loads(art.to_json())
    assert data["method"] == "two_gamma"
    assert data["gamma_lo"] == pytest.approx(1.63)
    assert data["gamma_hi"] == pytest.approx(0.47)
    assert data["pivot"] == pytest.approx(0.15)
    assert data["version"] == "pcal-v1" and data["n_races"] == 3200
    assert data["as_of"] == "2024-12-31"
