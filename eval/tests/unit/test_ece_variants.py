"""T009: equal-mass (tie-safe) / band / field-size ECE (FR-006, codex C10)."""

from __future__ import annotations

import numpy as np

from horseracing_eval.metrics import ece_by_prob_band, ece_equal_mass


def test_equal_mass_bins_are_roughly_equal_count():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, size=100)
    y = (rng.uniform(0, 1, size=100) < p).astype(int)
    out = ece_equal_mass(p, y, bins=10)
    assert out["n_bins"] >= 1
    assert sum(out["bin_counts"]) == 100
    # no bin should be wildly larger than the target when there are no ties
    assert max(out["bin_counts"]) <= 100 // 10 + 5


def test_equal_mass_tie_safe_does_not_split_a_plateau():
    # 20 identical probs (a plateau) + spread. A tied plateau must land in ONE bin,
    # so at least one bin has >= 20 rows (the plateau is never split across a boundary).
    p = np.array([0.5] * 20 + list(np.linspace(0.0, 1.0, 20)))
    y = np.zeros(len(p), dtype=int)
    out = ece_equal_mass(p, y, bins=8)
    assert sum(out["bin_counts"]) == len(p)
    assert max(out["bin_counts"]) >= 20


def test_equal_mass_empty_is_zero():
    out = ece_equal_mass([], [], bins=10)
    assert out == {"ece": 0.0, "n_bins": 0, "bin_counts": []}


def test_equal_mass_perfect_calibration_is_zero():
    # prob p, realized rate exactly p within each plateau -> ECE 0
    p = np.array([0.0] * 10 + [1.0] * 10)
    y = np.array([0] * 10 + [1] * 10)
    out = ece_equal_mass(p, y, bins=4)
    assert out["ece"] == 0.0


def test_ece_by_prob_band_uses_fixed_edges():
    p = np.array([0.02, 0.10, 0.20, 0.50])
    y = np.array([0, 0, 1, 1])
    out = ece_by_prob_band(p, y, band_edges=[0.05, 0.15, 0.30])
    # 4 bands: [0,.05) [.05,.15) [.15,.30) [.30,1]
    assert set(out.keys()) == {"[0.00,0.05)", "[0.05,0.15)", "[0.15,0.30)", "[0.30,1.00]"}


def test_ece_by_prob_band_omits_empty_bands():
    p = np.array([0.5, 0.6])
    y = np.array([1, 0])
    out = ece_by_prob_band(p, y, band_edges=[0.05, 0.15, 0.30])
    assert list(out.keys()) == ["[0.30,1.00]"]
