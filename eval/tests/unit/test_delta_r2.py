"""ΔR² core: the definition, the nesting properties, and the traps that make it lie.

The instrument exists to answer "does p add information q lacks", so the tests that matter are
the ones that force ΔR² to be ZERO when p adds nothing, and the ones that stop the denominator
or the fit window from quietly doing the work.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from horseracing_eval.bootstrap import race_day_cluster_ratio_bootstrap_ci_v1
from horseracing_eval.delta_r2 import (
    DeltaR2Race,
    evaluate_delta_r2,
    prepare_races,
)


def _race(rid, day, block, p, q, winner=0):
    return DeltaR2Race(rid, day, block, winner, np.asarray(p, float), np.asarray(q, float))


def _uniform_set(n_blocks=3, races_per_block=40, field=8, seed=0):
    """p and q both uniform → every model is the null → all R² = 0, ΔR² = 0."""
    rng = np.random.default_rng(seed)
    out = []
    for b in range(n_blocks):
        for i in range(races_per_block):
            u = np.full(field, 1.0 / field)
            out.append(_race(f"{b}-{i}", f"20{20 + b}-01-{i % 28 + 1:02d}", f"20{20 + b}",
                             u, u, winner=int(rng.integers(field))))
    return out


# --- definition -------------------------------------------------------------------------------

def test_uniform_predictor_has_r2_zero_at_any_field_size():
    """R² = 1 − D/D₀ with D₀ = Σ log N_r: a uniform predictor must score exactly 0 even when
    field sizes differ (the classic bug is normalising by log(mean N))."""
    races = []
    for i, n in enumerate([6, 12, 18] * 20):
        u = np.full(n, 1.0 / n)
        blk = "2021" if i < 30 else "2022"
        races.append(_race(f"r{i}", f"{blk}-01-{i % 28 + 1:02d}", blk, u, u, winner=i % n))
    rep = evaluate_delta_r2(races, b=50, seed=1)
    assert rep.r2_model == pytest.approx(0.0, abs=1e-12)
    assert rep.r2_market_raw == pytest.approx(0.0, abs=1e-12)
    assert rep.mean_log_field == pytest.approx(
        float(np.mean([math.log(len(r.p)) for r in races if r.block == "2022"])), abs=1e-12
    )


def test_denominator_is_sum_log_field_not_log_mean_field():
    """Hand-checked: two scored races with fields 4 and 16 → D₀ = log4 + log16."""
    races = [
        _race("w", "2020-01-01", "2020", [0.25] * 4, [0.25] * 4),
        _race("a", "2021-01-01", "2021", [0.4, 0.2, 0.2, 0.2], [0.4, 0.2, 0.2, 0.2]),
        _race("b", "2021-01-02", "2021", [1 / 16] * 16, [1 / 16] * 16),
    ]
    rep = evaluate_delta_r2(races, b=10, seed=1)
    assert rep.n_races == 2
    assert rep.mean_log_field == pytest.approx((math.log(4) + math.log(16)) / 2, abs=1e-12)


def test_perfect_predictor_approaches_r2_one():
    races = []
    for b, blk in enumerate(["2021", "2022"]):
        for i in range(30):
            p = np.full(8, 1e-9)
            p[i % 8] = 1.0
            races.append(_race(f"{b}{i}", f"{blk}-01-{i % 28 + 1:02d}", blk, p, p, winner=i % 8))
    rep = evaluate_delta_r2(races, b=20, seed=1)
    assert rep.r2_model > 0.99


# --- the property the instrument exists for ---------------------------------------------------

def test_delta_r2_is_zero_when_p_equals_q():
    """p carrying nothing q lacks must produce ΔR² = 0 — the whole point of the instrument."""
    rng = np.random.default_rng(7)
    races = []
    for b, blk in enumerate(["2021", "2022", "2023"]):
        for i in range(60):
            v = rng.random(9) + 0.1
            v /= v.sum()
            races.append(_race(f"{b}-{i}", f"{blk}-02-{i % 28 + 1:02d}", blk, v, v,
                               winner=int(rng.integers(9))))
    rep = evaluate_delta_r2(races, b=50, seed=3)
    assert rep.delta_r2_model_given_market == pytest.approx(0.0, abs=1e-9)


def test_delta_r2_is_zero_when_p_is_race_uniform():
    """A within-race constant p cannot shift a race-internal softmax — it must score exactly 0."""
    rng = np.random.default_rng(11)
    races = []
    for b, blk in enumerate(["2021", "2022", "2023"]):
        for i in range(60):
            q = rng.random(10) + 0.1
            q /= q.sum()
            races.append(_race(f"{b}-{i}", f"{blk}-03-{i % 28 + 1:02d}", blk,
                               np.full(10, 0.1), q, winner=int(rng.integers(10))))
    rep = evaluate_delta_r2(races, b=50, seed=3)
    assert rep.delta_r2_model_given_market == pytest.approx(0.0, abs=1e-9)


def test_informative_p_recovers_a_positive_delta_r2():
    """When p genuinely carries the winner signal and q does not, ΔR² must be clearly positive."""
    rng = np.random.default_rng(5)
    races = []
    for b, blk in enumerate(["2021", "2022", "2023", "2024"]):
        for i in range(150):
            n = 8
            w = int(rng.integers(n))
            q = np.full(n, 1.0 / n)
            p = np.full(n, 0.05)
            p[w] = 0.65                      # p knows something q does not
            p /= p.sum()
            races.append(_race(f"{b}-{i}", f"{blk}-04-{i % 28 + 1:02d}", blk, p, q, winner=w))
    rep = evaluate_delta_r2(races, b=200, seed=9)
    assert rep.delta_r2_model_given_market > 0.1
    assert rep.ci_model_given_market.ci_low > 0
    assert rep.verdict in {"evidence_positive", "material_positive"}


def test_market_own_recalibration_is_not_credited_to_the_model():
    """q mis-scaled by a power: the reduced model absorbs it, so ΔR²_conditional stays ~0 while
    the LITERAL ΔR² is inflated. This separation is the review's main correction."""
    rng = np.random.default_rng(13)
    races = []
    for b, blk in enumerate(["2021", "2022", "2023"]):
        for i in range(200):
            n = 10
            true = rng.random(n) + 0.05
            true /= true.sum()
            w = int(rng.choice(n, p=true))
            q = true ** 0.5                    # market needs γ≈2 to be well calibrated
            q /= q.sum()
            races.append(_race(f"{b}-{i}", f"{blk}-05-{i % 28 + 1:02d}", blk, q.copy(), q,
                               winner=w))
    rep = evaluate_delta_r2(races, b=100, seed=4)
    assert rep.delta_r2_literal > 1e-3, "power miscalibration should inflate the literal metric"
    assert rep.delta_r2_model_given_market == pytest.approx(0.0, abs=1e-9)


# --- prequential discipline --------------------------------------------------------------------

def test_first_block_is_fit_only_and_never_scored():
    races = _uniform_set(n_blocks=3, races_per_block=10)
    rep = evaluate_delta_r2(races, b=10, seed=1)
    assert rep.n_races == 20                      # 3 blocks, first one withheld
    assert rep.n_blocks_scored == 2
    assert [f.block for f in rep.fits] == ["2021", "2022"]


def test_single_block_cannot_be_scored():
    races = _uniform_set(n_blocks=1, races_per_block=10)
    with pytest.raises(ValueError, match="at least two blocks"):
        evaluate_delta_r2(races, b=10, seed=1)


def test_fit_window_is_strictly_before_the_scored_window_by_day():
    """A block whose fit data shares a day with the scored data must fail closed, not silently
    score a race with coefficients that saw its own race-day."""
    races = [
        _race("a", "2021-06-01", "2021", [0.5, 0.5], [0.5, 0.5]),
        _race("b", "2021-06-05", "2022", [0.5, 0.5], [0.5, 0.5]),  # earlier day, later block
        _race("c", "2021-05-01", "2022", [0.5, 0.5], [0.5, 0.5]),
    ]
    with pytest.raises(ValueError, match="overlaps"):
        evaluate_delta_r2(races, b=10, seed=1)


def test_changing_a_scored_label_does_not_change_earlier_fits():
    base = _uniform_set(n_blocks=3, races_per_block=20, seed=2)
    rep_a = evaluate_delta_r2(base, b=10, seed=1)
    mutated = list(base)
    last = mutated[-1]
    mutated[-1] = DeltaR2Race(last.race_id, last.day, last.block,
                              (last.winner_idx + 1) % last.p.size, last.p, last.q)
    rep_b = evaluate_delta_r2(mutated, b=10, seed=1)
    assert [(f.alpha, f.beta, f.gamma) for f in rep_a.fits] == \
           [(f.alpha, f.beta, f.gamma) for f in rep_b.fits]


# --- preprocessing / fail-closed ----------------------------------------------------------------

def test_zero_probability_is_floored_and_counted_not_infinite():
    races = _uniform_set(n_blocks=2, races_per_block=5)
    r = races[-1]
    p = r.p.copy()
    p[0] = 0.0
    races[-1] = DeltaR2Race(r.race_id, r.day, r.block, 1, p, r.q)
    rep = evaluate_delta_r2(races, b=10, seed=1)
    assert rep.n_floored == 1
    assert math.isfinite(rep.r2_model)


def test_mismatched_field_sizes_fail_closed():
    races = _uniform_set(n_blocks=2, races_per_block=3)
    r = races[-1]
    races[-1] = DeltaR2Race(r.race_id, r.day, r.block, 0, np.full(4, 0.25), r.q)
    with pytest.raises(ValueError, match="different fields"):
        evaluate_delta_r2(races, b=10, seed=1)


@pytest.mark.parametrize("bad", [np.array([0.5, np.nan]), np.array([-0.1, 1.1])])
def test_non_finite_or_out_of_range_probabilities_fail_closed(bad):
    with pytest.raises(ValueError):
        prepare_races([_race("x", "2021-01-01", "2021", bad, [0.5, 0.5])])


def test_winner_index_out_of_range_fails_closed():
    with pytest.raises(ValueError, match="winner_idx"):
        prepare_races([DeltaR2Race("x", "2021-01-01", "2021", 5,
                                   np.full(3, 1 / 3), np.full(3, 1 / 3))])


# --- determinism / ratio bootstrap ---------------------------------------------------------------

def test_same_seed_gives_identical_intervals():
    races = _uniform_set(n_blocks=3, races_per_block=25, seed=6)
    a = evaluate_delta_r2(races, b=200, seed=42)
    b = evaluate_delta_r2(races, b=200, seed=42)
    assert (a.ci_model_given_market.ci_low, a.ci_model_given_market.ci_high) == \
           (b.ci_model_given_market.ci_low, b.ci_model_given_market.ci_high)


def test_ratio_bootstrap_recomputes_the_denominator_each_replicate():
    """Days with very different field sizes: a fixed denominator would understate the spread."""
    num = {"d1": [1.0] * 5, "d2": [1.0] * 5}
    den = {"d1": [math.log(4)] * 5, "d2": [math.log(18)] * 5}
    ci = race_day_cluster_ratio_bootstrap_ci_v1(num, den, b=2000, seed=1)
    lo_all, hi_all = 1 / math.log(18), 1 / math.log(4)
    assert ci.ci_low == pytest.approx(lo_all, abs=1e-9)
    assert ci.ci_high == pytest.approx(hi_all, abs=1e-9)
    assert ci.ci_low < ci.point < ci.ci_high


def test_ratio_bootstrap_rejects_misaligned_inputs():
    with pytest.raises(ValueError, match="same race-days"):
        race_day_cluster_ratio_bootstrap_ci_v1({"d1": [1.0]}, {"d2": [1.0]}, b=10)
    with pytest.raises(ValueError, match="race counts differ"):
        race_day_cluster_ratio_bootstrap_ci_v1({"d1": [1.0, 2.0]}, {"d1": [1.0]}, b=10)


def test_fewer_than_two_days_is_no_decision():
    ci = race_day_cluster_ratio_bootstrap_ci_v1({"d1": [1.0]}, {"d1": [2.0]}, b=10)
    assert ci.no_decision and ci.ci_low is None


def test_float_noise_around_exact_zero_is_not_a_verdict():
    """p == q gives ΔR² = 0 up to float noise; a degenerate CI at -1e-17 must read NO_DECISION,
    not `harmful`. Directional verdicts need a real effect, not a rounding artefact."""
    rng = np.random.default_rng(21)
    races = []
    for b, blk in enumerate(["2021", "2022", "2023"]):
        for i in range(40):
            v = rng.random(8) + 0.1
            v /= v.sum()
            races.append(_race(f"{b}-{i}", f"{blk}-06-{i % 28 + 1:02d}", blk, v, v,
                               winner=int(rng.integers(8))))
    rep = evaluate_delta_r2(races, b=100, seed=2)
    assert rep.verdict == "NO_DECISION"
