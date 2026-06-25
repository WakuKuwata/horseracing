"""T007 (013): fit_power_gamma — normalized winner-likelihood MLE, degeneracy, determinism."""

from __future__ import annotations

from horseracing_probability.fl_bias import GAMMA_MAX, GAMMA_MIN, apply_g, fit_power_gamma


def _race_odds(qtrue):
    """Build win_odds whose implied vote share equals qtrue (odds_i = 1/q_i, ignoring overround)."""
    return {h: 1.0 / p for h, p in qtrue.items()}


def _synth_samples(n, gamma_true, winners_per_q):
    """Synthetic races where the realized winner is drawn deterministically by q^gamma_true rank.

    winners_per_q maps a race index to which rank wins, emulating an FL-biased market that
    under-rates favorites: the favorite (by q^gamma_true) wins more often than its q suggests.
    """
    samples = []
    base = {"A": 0.45, "B": 0.28, "C": 0.17, "D": 0.10}
    for i in range(n):
        odds = _race_odds(base)
        # corrected probs q^gamma_true: favorite wins on most races
        corrected = apply_g("power", {"gamma": gamma_true}, base)
        winner = max(corrected, key=corrected.get) if winners_per_q(i) else min(corrected, key=corrected.get)
        samples.append((odds, winner))
    return samples


def test_recovers_gamma_gt_one_when_favorites_overperform():
    # favorites win far more than their q -> γ should be pushed above 1
    samples = _synth_samples(200, gamma_true=2.0, winners_per_q=lambda i: i % 5 != 0)
    gamma, n_info = fit_power_gamma(samples)
    assert n_info > 0
    assert GAMMA_MIN <= gamma <= GAMMA_MAX
    assert gamma > 1.0  # favorite over-performance -> sharpen


def test_flat_q_is_non_informative():
    # all-equal q -> gradient in gamma ~ 0; excluded from informative set
    flat = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
    samples = [({h: 1.0 / p for h, p in flat.items()}, "A") for _ in range(10)]
    gamma, n_info = fit_power_gamma(samples)
    assert n_info == 0 and gamma == 1.0  # identity fallback


def test_no_informative_races_falls_back_to_identity():
    gamma, n_info = fit_power_gamma([({"A": 2.0, "B": 3.0}, None)])  # no winner
    assert gamma == 1.0 and n_info == 0


def test_single_runner_excluded():
    gamma, n_info = fit_power_gamma([({"A": 2.0}, "A")])  # <2 valid -> non-informative
    assert n_info == 0 and gamma == 1.0


def test_deterministic():
    samples = _synth_samples(120, gamma_true=1.6, winners_per_q=lambda i: i % 4 != 0)
    a, _ = fit_power_gamma(samples)
    b, _ = fit_power_gamma(samples)
    assert a == b
