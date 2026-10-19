"""Feature 141: the shared market-mixture definition (horseracing_eval.market_mix)."""

from __future__ import annotations

import json
import math
import pathlib

import numpy as np
import pytest

from horseracing_eval import market_mix as mm
from horseracing_eval.attention_rules import DISPLAYED_MARKET_EV_MODEL_VERSION

_FIT = (
    pathlib.Path(__file__).resolve().parents[3]
    / "scripts/roi_explore/market_mix_20261019/coefficients.json"
)


def _hand(p, o, a, b):
    inv = [1 / x for x in o]
    s = [a * math.log(i / sum(inv)) + b * math.log(pi) for i, pi in zip(inv, p, strict=True)]
    z = sum(math.exp(v) for v in s)
    return [math.exp(v) / z for v in s]


def test_constants():
    assert mm.MIX_VERSION == "mix-ens15-v1"
    assert mm.MIX_EV_THRESHOLD == 1.0
    assert mm.MIX_SOURCE_MODEL_VERSION == DISPLAYED_MARKET_EV_MODEL_VERSION == "mev-ens15-v1"
    assert set(mm.MIX_COEFFICIENTS) == {2025, 2026}
    for y, c in mm.MIX_COEFFICIENTS.items():
        assert c.year == y and c.fit_through_year == y - 1 and c.fit_from_year == 2010
        assert 0 < c.a < 1 and 0 < c.b < 1


def test_mix_matches_a_hand_computation_and_sums_to_one():
    c = mm.coefficients_for(2026)
    p, o = [0.36, 0.25, 0.16, 0.09], [2.5, 4.0, 8.0, 15.0]
    got = mm.mix_probabilities(p, o, c)
    assert got == pytest.approx(_hand(p, o, c.a, c.b), rel=1e-13)
    assert got.sum() == pytest.approx(1.0, abs=1e-15)


def test_race_level_rescaling_of_p_cancels():
    c = mm.coefficients_for(2026)
    p, o = np.array([0.36, 0.25, 0.16, 0.09]), [2.5, 4.0, 8.0, 15.0]
    np.testing.assert_allclose(mm.mix_probabilities(p, o, c), mm.mix_probabilities(p * 3.7, o, c),
                               rtol=1e-14)


def test_limits_reduce_to_market_and_model():
    p, o = [0.5, 0.3, 0.2], [2.0, 4.0, 8.0]
    q = np.array([1 / x for x in o]) / sum(1 / x for x in o)
    np.testing.assert_allclose(np.exp(mm.mix_log_probabilities(p, o, 1.0, 0.0)), q, rtol=1e-14)
    np.testing.assert_allclose(np.exp(mm.mix_log_probabilities(p, o, 0.0, 1.0)), p, rtol=1e-14)


def test_numerically_stable_for_tiny_probabilities():
    c = mm.coefficients_for(2026)
    out = mm.mix_probabilities([1e-300, 0.5, 0.5], [999.9, 2.0, 2.0], c)
    assert np.all(np.isfinite(out)) and out.sum() == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("p", "o"),
    [([0.5], [2.0]), ([0.5, 0.0], [2.0, 3.0]), ([0.5, float("nan")], [2.0, 3.0]),
     ([0.5, 0.5], [2.0, 0.9]), ([0.5, 0.5], [2.0]), ([0.5, -0.1], [2.0, 3.0])],
)
def test_invalid_inputs_raise(p, o):
    with pytest.raises(ValueError):
        mm.mix_probabilities(p, o, mm.coefficients_for(2026))


def test_unknown_year_has_no_coefficients():
    assert mm.coefficients_for(2027) is None and mm.coefficients_for(2024) is None


def test_threshold_is_strict():
    assert mm.exceeds_mix_threshold(1.0) is False
    assert mm.exceeds_mix_threshold(1.0 + 1e-12) is True


@pytest.mark.skipif(not _FIT.exists(), reason="fit output not present")
def test_market_mix_coefficients_match_fit():
    fit = json.loads(_FIT.read_text())
    assert fit["version"] == mm.MIX_VERSION
    assert fit["source_model_version"] == mm.MIX_SOURCE_MODEL_VERSION
    for y, c in mm.MIX_COEFFICIENTS.items():
        f = fit["coefficients"][str(y)]
        assert (c.a, c.b) == (f["a"], f["b"])
        assert (c.fit_from_year, c.fit_through_year) == (f["fit_from_year"], f["fit_through_year"])
        assert (c.n_races, c.n_rows) == (f["n_races"], f["n_rows"])
        assert c.source_sha256 == fit["source_sha256"]
