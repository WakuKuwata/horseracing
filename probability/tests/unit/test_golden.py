"""US1 (SC-001): hand-computed golden values (codex-verified) for N=3, plus N=4 normalization."""

from __future__ import annotations

import pytest

from horseracing_probability.engine import joint_probabilities

_ATOL = 1e-9


def test_golden_n3():
    # p = A:0.5, B:0.3, C:0.2 (codex-verified values)
    jp = joint_probabilities({"A": 0.5, "B": 0.3, "C": 0.2})

    assert jp.exacta[("A", "B")] == pytest.approx(0.30, abs=_ATOL)
    assert jp.exacta[("A", "C")] == pytest.approx(0.20, abs=_ATOL)
    assert jp.exacta[("B", "A")] == pytest.approx(3 / 14, abs=_ATOL)
    assert jp.exacta[("B", "C")] == pytest.approx(3 / 35, abs=_ATOL)
    assert jp.exacta[("C", "A")] == pytest.approx(0.125, abs=_ATOL)
    assert jp.exacta[("C", "B")] == pytest.approx(0.075, abs=_ATOL)
    assert sum(jp.exacta.values()) == pytest.approx(1.0, abs=_ATOL)

    # N=3: trifecta(i,j,k) == exacta(i,j) (last factor collapses to 1)
    assert jp.trifecta[("A", "B", "C")] == pytest.approx(0.30, abs=_ATOL)
    assert jp.trifecta[("C", "B", "A")] == pytest.approx(0.075, abs=_ATOL)
    assert sum(jp.trifecta.values()) == pytest.approx(1.0, abs=_ATOL)

    assert jp.quinella[frozenset(("A", "C"))] == pytest.approx(0.325, abs=_ATOL)
    # N=3 degenerate: the single trio and every wide pair are certain (top3 = all)
    assert jp.trio[frozenset(("A", "B", "C"))] == pytest.approx(1.0, abs=_ATOL)
    assert jp.wide[frozenset(("A", "B"))] == pytest.approx(1.0, abs=_ATOL)
    # N=3 (<=4 runners) -> no place bet
    assert jp.place is None


def test_n4_distributions_sum_to_one():
    jp = joint_probabilities({"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1})
    assert sum(jp.exacta.values()) == pytest.approx(1.0, abs=_ATOL)
    assert sum(jp.trifecta.values()) == pytest.approx(1.0, abs=_ATOL)


def test_unnormalized_input_is_renormalized():
    # raw counts proportional to 0.5/0.3/0.2 -> identical to normalized
    jp = joint_probabilities({"A": 5.0, "B": 3.0, "C": 2.0})
    assert jp.exacta[("A", "B")] == pytest.approx(0.30, abs=_ATOL)
    assert sum(jp.win.values()) == pytest.approx(1.0, abs=_ATOL)
