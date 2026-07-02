"""Feature 049: stage-discounted Harville derivation + λ fitting (INV-S1..S8)."""

from __future__ import annotations

import random

from horseracing_eval.baselines import harville_topk
from horseracing_eval.stage_discount import (
    IDENTITY,
    LAMBDA_MAX,
    LAMBDA_MIN,
    StageDiscount,
    TopkSample,
    discounted_topk,
    fit_stage_discount,
    logic_version_fragment,
)


def _norm(xs: list[float]) -> list[float]:
    s = sum(xs)
    return [x / s for x in xs]


def _fields() -> list[list[float]]:
    return [
        _norm([0.5, 0.3, 0.2]),
        _norm([0.4, 0.25, 0.2, 0.1, 0.05]),
        _norm([0.30, 0.20, 0.15, 0.12, 0.10, 0.08, 0.05]),
        _norm([10, 8, 6, 5, 4, 3, 2, 2, 1, 1]),
    ]


# ---- INV-S1: λ=1 byte-identical to legacy harville_topk ---------------------


def test_identity_lambda_byte_identical():
    for p in _fields():
        base2, base3 = harville_topk(p)
        d2, d3 = discounted_topk(p, IDENTITY)
        assert d2 == base2  # exact equality, not approx (INV-S1)
        assert d3 == base3


def test_harville_topk_lambda1_takes_legacy_branch():
    # passing explicit 1.0/1.0 must equal the no-arg call byte-for-byte
    for p in _fields():
        assert harville_topk(p, lambda2=1.0, lambda3=1.0) == harville_topk(p)


# ---- INV-S3/S4: monotone + sums under a range of λ --------------------------


def test_consistency_sums_and_monotone_across_lambda():
    for lam in (0.3, 0.7, 1.5, 5.0):
        sd = StageDiscount(lambda2=lam, lambda3=lam)
        for p in _fields():
            n = len(p)
            top2, top3 = discounted_topk(p, sd)
            # win <= top2 <= top3 <= 1
            for i in range(n):
                assert 0.0 <= p[i] <= top2[i] + 1e-12
                assert top2[i] <= top3[i] + 1e-12
                assert top3[i] <= 1.0 + 1e-9
            # Σtop2 ≈ 2, Σtop3 ≈ 3 (min(3,n) for tiny fields)
            assert abs(sum(top2) - min(2.0, n)) < 1e-6
            assert abs(sum(top3) - min(3.0, n)) < 1e-6


# ---- INV-S7: λ<1 lowers the favourite's top2/top3, lifts the longshot -------


def test_direction_lambda_below_one_compresses_top():
    p = _norm([0.45, 0.25, 0.15, 0.08, 0.04, 0.03])
    base2, base3 = harville_topk(p)
    sd = StageDiscount(lambda2=0.5, lambda3=0.5)
    d2, d3 = discounted_topk(p, sd)
    fav = 0  # highest win prob
    dog = len(p) - 1  # lowest
    assert d2[fav] < base2[fav]
    assert d3[fav] < base3[fav]
    assert d2[dog] > base2[dog]
    assert d3[dog] > base3[dog]


# ---- INV-S6: deterministic fit + fallbacks ----------------------------------


def _pl_draw(win: list[float], rng: random.Random) -> tuple[int, int, int]:
    """Sample 1st/2nd/3rd by Plackett-Luce (sampling without replacement) so that
    finishers carry realistic noise — a deterministic top-k order would push the
    fitted λ to the boundary (correctly), which is not what we want to exercise."""
    idx = list(range(len(win)))
    picked = []
    for _ in range(3):
        w = [win[i] for i in idx]
        tot = sum(w)
        r = rng.random() * tot
        acc = 0.0
        for pos, weight in enumerate(w):
            acc += weight
            if r <= acc:
                picked.append(idx.pop(pos))
                break
        else:  # pragma: no cover
            picked.append(idx.pop())
    return picked[0], picked[1], picked[2]


def _samples(n: int, seed: int = 0) -> list[TopkSample]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        k = rng.choice([6, 8, 10, 12])
        raw = [rng.random() + 0.05 for _ in range(k)]
        win = tuple(_norm(raw))
        i1, i2, i3 = _pl_draw(list(win), rng)
        out.append(TopkSample(win=win, i1=i1, i2=i2, i3=i3))
    return out


def test_fit_deterministic():
    s = _samples(400, seed=7)
    a = fit_stage_discount(s)
    b = fit_stage_discount(s)
    assert (a.lambda2, a.lambda3) == (b.lambda2, b.lambda3)
    assert not a.fallback
    assert LAMBDA_MIN <= a.lambda2 <= LAMBDA_MAX
    assert LAMBDA_MIN <= a.lambda3 <= LAMBDA_MAX


def test_fit_insufficient_samples_falls_back_to_identity():
    sd = fit_stage_discount(_samples(50), min_races=300)
    assert sd.fallback
    assert sd.is_identity
    assert sd.n_races_l2 == 50


def test_fit_excludes_non_unique_finishers():
    # a race with a missing 2nd (dead heat) counts toward stage-3 requirement? No:
    # stage-2 sample needs i1 AND i2; stage-3 needs i1,i2,i3. Drop where None.
    good = _samples(300, seed=3)
    dead = [TopkSample(win=s.win, i1=s.i1, i2=None, i3=None) for s in _samples(100, seed=9)]
    sd = fit_stage_discount(good + dead, min_races=300)
    # only the 300 'good' contribute both stages
    assert sd.n_races_l2 == 300
    assert sd.n_races_l3 == 300


# ---- INV-S8 partial (leak boundary): empty fit sample -> identity -----------


def test_empty_samples_identity():
    sd = fit_stage_discount([], min_races=300)
    assert sd.is_identity and sd.fallback


def test_cutoff_leaves_no_samples_yields_identity():
    # INV-S8 leak boundary: if the walk-forward cutoff excludes every prior race (e.g. the
    # target is the first race), the fitter sees an empty sample set -> identity (never peeks
    # at same/after-cutoff results).
    sd = fit_stage_discount([], min_races=1)
    assert sd.is_identity and sd.fallback


# ---- logic_version fragment (data-model) ------------------------------------


def test_logic_version_fragment():
    assert logic_version_fragment(None) == ""
    assert logic_version_fragment(IDENTITY) == "sdisc=identity"
    frag = logic_version_fragment(StageDiscount(lambda2=0.5, lambda3=0.6, n_races_l2=400, n_races_l3=390))
    assert frag.startswith("sdisc=harville;l2=0.50000;l3=0.60000;n2=400;n3=390")
