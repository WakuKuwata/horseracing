"""US1 (SC-002): consistency invariants incl. joint marginals == harville_topk."""

from __future__ import annotations

import pytest
from horseracing_eval.baselines import harville_topk

from horseracing_probability.consistency import check_joint_consistency
from horseracing_probability.engine import joint_probabilities

_CASES = [
    {"A": 0.5, "B": 0.3, "C": 0.2},
    {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1},
    {chr(65 + i): 1.0 / 8 for i in range(8)},                 # uniform 8
    {"A": 0.7, "B": 0.15, "C": 0.1, "D": 0.04, "E": 0.01},    # skewed 5
]


@pytest.mark.parametrize("win", _CASES)
def test_check_passes(win):
    jp = joint_probabilities(win)
    check_joint_consistency(jp)  # must not raise


@pytest.mark.parametrize("win", _CASES)
def test_relations(win):
    jp = joint_probabilities(win)
    for key, q in jp.quinella.items():
        i, j = tuple(key)
        assert q == pytest.approx(jp.exacta[(i, j)] + jp.exacta[(j, i)], abs=1e-12)
        assert jp.wide[key] >= q - 1e-12   # wide (top3 pair) >= quinella (top2 pair)


@pytest.mark.parametrize("win", _CASES)
def test_marginals_match_harville(win):
    jp = joint_probabilities(win)
    ids = sorted(win)
    p = [jp.win[h] for h in ids]
    top2, top3 = harville_topk(p)
    for idx, h in enumerate(ids):
        marg2 = sum(jp.exacta[(h, o)] + jp.exacta[(o, h)] for o in ids if o != h)
        marg3 = sum(v for trip, v in jp.trifecta.items() if h in trip)
        assert marg2 == pytest.approx(top2[idx], abs=1e-9)   # exacta marginal == harville top2
        assert marg3 == pytest.approx(top3[idx], abs=1e-9)   # trifecta marginal == harville top3


def test_monotonicity_of_place():
    jp = joint_probabilities({chr(65 + i): v for i, v in enumerate([0.4, 0.25, 0.2, 0.1, 0.05])},
                             field_size=6)
    # higher win prob -> higher place prob
    assert jp.place["A"] >= jp.place["B"] >= jp.place["C"] >= jp.place["D"] >= jp.place["E"]
