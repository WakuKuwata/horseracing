"""T005: model p→p' calibrator fit/apply (Feature 017, SC-003/SC-004)."""

from __future__ import annotations

import math

from horseracing_probability.model_calibration import (
    apply_p_calibrator,
    fit_p_calibrator,
    fit_power_gamma,
)


def _synth_samples(gamma_true: float, n: int = 400):
    """Generate (p_dict, winner) where the winner is drawn deterministically to favor a gamma.

    We build races with a fixed prob vector and assign the winner as the top horse for a fraction of
    races and a mid horse otherwise, so the MLE recovers a non-trivial gamma. Deterministic (no RNG):
    winner chosen by index parity.
    """
    base = {"a": 0.5, "b": 0.3, "c": 0.2}
    out = []
    for i in range(n):
        # favorite wins 60% (i%5<3), second 30%, third 10% — overconfident vs base would push gamma
        if i % 10 < 6:
            w = "a"
        elif i % 10 < 9:
            w = "b"
        else:
            w = "c"
        out.append((dict(base), w))
    return out


def test_apply_is_normalized_and_engine_consistent():
    cal = fit_p_calibrator([({"a": 0.5, "b": 0.3, "c": 0.2}, "a")] * 60, method="power",
                           min_races=1, min_wins=1)
    pp = apply_p_calibrator({"a": 0.5, "b": 0.3, "c": 0.2}, cal)
    assert math.isclose(sum(pp.values()), 1.0, abs_tol=1e-9)
    # idempotent under re-application of the engine normalize (evaluated == engine vector)
    pp2 = apply_p_calibrator(pp, cal)
    assert math.isclose(sum(pp2.values()), 1.0, abs_tol=1e-9)


def test_gamma_below_one_softens_overconfidence():
    # gamma<1 should reduce the top prob (soften), gamma>1 sharpen.
    p = {"a": 0.6, "b": 0.25, "c": 0.15}
    soft = apply_p_calibrator(p, fit_p_calibrator([], method="power", min_races=1, min_wins=0))
    # empty samples → identity fallback (gamma=1): unchanged ranking, normalized
    assert max(soft, key=soft.get) == "a"
    # force gamma=0.5 via a calibrator dict
    from horseracing_probability.model_calibration import PCalibrator
    cal = PCalibrator(method="power", params={"gamma": 0.5}, train_window=None, n_races=0,
                      n_samples=0, prob_range=(0.0, 1.0), select="mle", base_model_version=None,
                      logic_version="t", sufficient=True)
    softened = apply_p_calibrator(p, cal)
    assert softened["a"] < p["a"] / sum(p.values()) + 1e-9  # top mass reduced vs raw-normalized
    assert max(softened, key=softened.get) == "a"           # ranking preserved


def test_ranking_preserved_by_power():
    from horseracing_probability.model_calibration import PCalibrator
    p = {"a": 0.5, "b": 0.3, "c": 0.2}
    for g in (0.3, 0.7, 1.0, 1.5, 2.0):
        cal = PCalibrator(method="power", params={"gamma": g}, train_window=None, n_races=0,
                          n_samples=0, prob_range=(0.0, 1.0), select="mle",
                          base_model_version=None, logic_version="t", sufficient=True)
        pp = apply_p_calibrator(p, cal)
        assert pp["a"] > pp["b"] > pp["c"]   # monotone power preserves order


def test_insufficient_data_falls_back_to_identity():
    cal = fit_p_calibrator(_synth_samples(1.0, n=10), method="power", min_races=50, min_wins=30)
    assert cal.sufficient is False
    assert cal.method == "identity"
    assert cal.params["gamma"] == 1.0


def test_fit_is_deterministic():
    s = _synth_samples(1.0, n=200)
    g1, n1 = fit_power_gamma(s)
    g2, n2 = fit_power_gamma(s)
    assert g1 == g2 and n1 == n2
    c1 = fit_p_calibrator(s, method="power", min_races=1, min_wins=1)
    c2 = fit_p_calibrator(s, method="power", min_races=1, min_wins=1)
    assert c1.params == c2.params and c1.logic_version == c2.logic_version
