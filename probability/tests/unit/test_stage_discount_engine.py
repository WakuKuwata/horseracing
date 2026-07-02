"""Feature 049: stage-discounted joint engine (INV-S4/S5/S9)."""

from __future__ import annotations

import pytest
from horseracing_eval.stage_discount import StageDiscount, discounted_topk

from horseracing_probability.consistency import check_joint_consistency
from horseracing_probability.engine import joint_probabilities

_CASES = [
    {"A": 0.5, "B": 0.3, "C": 0.2},
    {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1},
    {chr(65 + i): 1.0 / 8 for i in range(8)},
    {"A": 0.7, "B": 0.15, "C": 0.1, "D": 0.04, "E": 0.01},
]

_SDS = [StageDiscount(lambda2=0.4, lambda3=0.4), StageDiscount(lambda2=0.7, lambda3=1.3)]


# ---- INV-S9: None / identity is byte-identical to the legacy engine ----------


@pytest.mark.parametrize("win", _CASES)
def test_none_is_byte_identical(win):
    base = joint_probabilities(win)
    ident = joint_probabilities(win, stage_discount=StageDiscount())  # identity
    assert base.win == ident.win
    assert base.exacta == ident.exacta       # exact dict equality (INV-S9)
    assert base.trifecta == ident.trifecta
    assert base.quinella == ident.quinella
    assert base.trio == ident.trio
    assert base.wide == ident.wide
    assert base.place == ident.place


# ---- INV-S4: sums hold under discount ----------------------------------------


@pytest.mark.parametrize("win", _CASES)
@pytest.mark.parametrize("sd", _SDS)
def test_sums_under_discount(win, sd):
    jp = joint_probabilities(win, stage_discount=sd)
    assert sum(jp.exacta.values()) == pytest.approx(1.0, abs=1e-9)
    if len(win) >= 3:
        assert sum(jp.trifecta.values()) == pytest.approx(1.0, abs=1e-9)


# ---- INV-S5: discounted joint marginals == discounted harville_topk ----------


@pytest.mark.parametrize("win", _CASES)
@pytest.mark.parametrize("sd", _SDS)
def test_marginals_match_discounted_harville(win, sd):
    jp = joint_probabilities(win, stage_discount=sd)
    ids = sorted(win)
    p = [jp.win[h] for h in ids]
    top2, top3 = discounted_topk(p, sd)
    for idx, h in enumerate(ids):
        marg2 = sum(jp.exacta[(h, o)] + jp.exacta[(o, h)] for o in ids if o != h)
        assert marg2 == pytest.approx(top2[idx], abs=1e-9)
        if len(win) >= 3:
            marg3 = sum(v for trip, v in jp.trifecta.items() if h in trip)
            assert marg3 == pytest.approx(top3[idx], abs=1e-9)


@pytest.mark.parametrize("win", _CASES)
@pytest.mark.parametrize("sd", _SDS)
def test_consistency_checker_accepts_discounted_joint(win, sd):
    jp = joint_probabilities(win, stage_discount=sd)
    check_joint_consistency(jp, stage_discount=sd)  # must not raise (INV-S5)


def test_consistency_checker_rejects_wrong_lambda():
    win = {"A": 0.5, "B": 0.3, "C": 0.2}
    jp = joint_probabilities(win, stage_discount=StageDiscount(lambda2=0.4, lambda3=0.4))
    # verifying against the WRONG λ (identity) must fail the marginal check
    with pytest.raises(Exception):
        check_joint_consistency(jp, stage_discount=None)


# ---- quinella/trio/wide still = orderings under discount ----------------------


@pytest.mark.parametrize("win", _CASES)
def test_relations_under_discount(win):
    sd = StageDiscount(lambda2=0.5, lambda3=0.5)
    jp = joint_probabilities(win, stage_discount=sd)
    for key, q in jp.quinella.items():
        i, j = tuple(key)
        assert q == pytest.approx(jp.exacta[(i, j)] + jp.exacta[(j, i)], abs=1e-12)
        if jp.wide is not None:
            assert jp.wide[key] >= q - 1e-12


# ---- place uses discounted top2/top3 per field-size rule ----------------------


def test_place_uses_discount():
    win = {chr(65 + i): v for i, v in enumerate([0.3, 0.2, 0.15, 0.12, 0.1, 0.08, 0.05])}
    sd = StageDiscount(lambda2=0.5, lambda3=0.5)
    base = joint_probabilities(win)  # field 7 -> place = top2
    disc = joint_probabilities(win, stage_discount=sd)
    assert base.place is not None and disc.place is not None
    # discount changes the favourite's place prob (top2 for field size 7)
    assert disc.place["A"] != base.place["A"]
