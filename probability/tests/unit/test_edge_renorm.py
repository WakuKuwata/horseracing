"""US1 (SC-003/004/006): renormalize, endpoints, determinism, place field-size, small fields."""

from __future__ import annotations

import pytest

from horseracing_probability.consistency import check_joint_consistency
from horseracing_probability.engine import joint_probabilities

_ATOL = 1e-9


def test_subpopulation_renormalizes():
    # caller excludes scratched -> engine gets only the running field and renormalizes
    full = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
    running = {k: full[k] for k in ("A", "B", "C")}  # D scratched (excluded by caller)
    jp = joint_probabilities(running)
    assert "D" not in jp.win                                  # scratched horse absent (prob 0)
    assert sum(jp.win.values()) == pytest.approx(1.0, abs=_ATOL)
    # running {0.4,0.3,0.2} renormalizes to {4/9,3/9,2/9}: exacta(A,B)=(4/9)(3/9)/(5/9)=4/15
    assert jp.win["A"] == pytest.approx(4 / 9, abs=_ATOL)
    assert jp.exacta[("A", "B")] == pytest.approx(4 / 15, abs=_ATOL)
    assert sum(jp.exacta.values()) == pytest.approx(1.0, abs=_ATOL)


def test_endpoint_no_zero_division():
    jp = joint_probabilities({"A": 0.999999, "B": 0.0000005, "C": 0.0000005})
    check_joint_consistency(jp)  # clip keeps denominators > 0
    assert sum(jp.exacta.values()) == pytest.approx(1.0, abs=1e-6)


def test_deterministic():
    win = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
    assert joint_probabilities(win) == joint_probabilities(win)


def test_place_field_size_rules():
    win = {chr(65 + i): 1.0 / 10 for i in range(10)}
    assert joint_probabilities(win, field_size=3).place is None      # <=4 -> none
    top2 = joint_probabilities(win, field_size=6).place              # 5-7 -> top2 inclusion
    top3 = joint_probabilities(win, field_size=10).place             # 8+  -> top3 inclusion
    # uniform: top3 inclusion > top2 inclusion per horse
    assert all(top3[h] >= top2[h] - 1e-9 for h in top2)


def test_small_field_degeneration():
    jp2 = joint_probabilities({"A": 0.6, "B": 0.4})
    assert jp2.trifecta == {} and jp2.trio == {}   # N<3 -> empty
    assert jp2.wide is None
    assert sum(jp2.exacta.values()) == pytest.approx(1.0, abs=_ATOL)
