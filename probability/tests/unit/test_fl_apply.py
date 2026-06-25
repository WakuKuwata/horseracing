"""T006 (013): apply_g — monotone, Σ=1, identity at γ=1, engine idempotence (SC-001/SC-003)."""

from __future__ import annotations

import math

from horseracing_probability.engine import DEFAULT_EPS, _normalize_clip
from horseracing_probability.fl_bias import apply_g
from horseracing_probability.market_odds import market_implied_win_probs

ODDS = {"A": 2.0, "B": 4.0, "C": 8.0, "D": 20.0}


def _q():
    return market_implied_win_probs(ODDS)


def test_sum_to_one_and_monotone():
    q = _q()
    qp = apply_g("power", {"gamma": 1.8}, q)
    assert math.isclose(sum(qp.values()), 1.0, abs_tol=1e-9)
    # order preserved (monotone): ranking by q == ranking by q'
    order_q = sorted(q, key=q.get)
    order_qp = sorted(qp, key=qp.get)
    assert order_q == order_qp


def test_gamma_one_is_identity():
    q = _q()
    qp = apply_g("power", {"gamma": 1.0}, q)
    for h in q:
        assert math.isclose(qp[h], q[h], abs_tol=1e-9)


def test_gamma_gt_one_strengthens_favorite():
    q = _q()
    fav = max(q, key=q.get)
    qp = apply_g("power", {"gamma": 2.0}, q)
    assert qp[fav] > q[fav]  # power γ>1 concentrates mass on the favorite


def test_engine_idempotent_no_op():
    q = _q()
    qp = apply_g("power", {"gamma": 1.7}, q)
    ids, p = _normalize_clip(qp, DEFAULT_EPS)
    reapplied = {ids[i]: p[i] for i in range(len(ids))}
    for h in qp:
        assert math.isclose(qp[h], reapplied[h], abs_tol=1e-12)  # q' already engine-normalized


def test_engine_idempotent_with_tiny_tail():
    # a near-zero tail sits at the engine clip floor (~eps); re-applying the engine normalize
    # changes values only by ~eps (negligible for pricing) — near-idempotent, "evaluated == used".
    q = market_implied_win_probs({"A": 1.2, "B": 3.0, "C": 5000.0})
    qp = apply_g("power", {"gamma": 2.5}, q)
    ids, p = _normalize_clip(qp, DEFAULT_EPS)
    reapplied = {ids[i]: p[i] for i in range(len(ids))}
    assert min(qp.values()) <= 2 * DEFAULT_EPS  # tail sits at the clip floor
    for h in qp:
        assert math.isclose(qp[h], reapplied[h], abs_tol=1e-8)  # deviation ~eps, negligible


def test_deterministic():
    q = _q()
    a = apply_g("power", {"gamma": 1.5}, q)
    b = apply_g("power", {"gamma": 1.5}, q)
    assert a == b


def test_unsupported_method_raises():
    import pytest
    with pytest.raises(NotImplementedError):
        apply_g("isotonic", {}, _q())
