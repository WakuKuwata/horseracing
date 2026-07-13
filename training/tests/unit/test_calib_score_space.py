"""T020: calibration score-space (research D3, IV, codex C7).

Verifies the asymmetry that C/D must respect:
- temperature on RAW logits ``softmax(s/T)`` equals power ``normalize(softmax(s)^(1/T))`` on the
  race-normalized p — the identity that lets isotonic/temperature act on raw score while
  race-normalized power acts on p, both landing Σ=1.
- power preserves order (no inversion) and Σ=1 (IV).
"""

from __future__ import annotations

import numpy as np
from horseracing_probability.model_calibration import _apply_gamma, _norm


def _softmax(s):
    e = np.exp(s - np.max(s))
    return e / e.sum()


def test_temperature_on_logits_equals_power_on_softmax_p():
    rng = np.random.default_rng(0)
    s = rng.normal(size=6)
    T = 1.7
    ids = [f"h{i}" for i in range(len(s))]
    # temperature on raw logits
    p_temp = _softmax(s / T)
    # power gamma=1/T on the race-normalized softmax p (probability's canonical apply)
    p = dict(zip(ids, _softmax(s), strict=True))
    p_power = _apply_gamma(p, 1.0 / T)
    for i, hid in enumerate(ids):
        assert p_power[hid] == np.float64(p_temp[i]) or abs(p_power[hid] - p_temp[i]) < 1e-12


def test_power_preserves_sum_one_and_order():
    p = {"a": 0.5, "b": 0.3, "c": 0.2}
    out = _apply_gamma(p, 0.6)  # gamma<1 softens but must not invert order
    assert abs(sum(out.values()) - 1.0) < 1e-12
    assert out["a"] > out["b"] > out["c"]
    assert all(0.0 < v < 1.0 for v in out.values())


def test_gamma_one_is_identity_after_normalization():
    p = {"a": 0.5, "b": 0.3, "c": 0.2}
    out = _apply_gamma(p, 1.0)
    assert out == _norm(p)


def test_power_is_finite_on_extreme_probs():
    p = {"a": 1.0 - 1e-9, "b": 1e-9}
    out = _apply_gamma(p, 2.0)
    assert all(np.isfinite(v) for v in out.values())
    assert abs(sum(out.values()) - 1.0) < 1e-12
