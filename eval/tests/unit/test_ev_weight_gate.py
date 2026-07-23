"""Feature 079 (step 4): paired EV-weight adoption gate — pure scorer.

Locks the verdict rule (ADOPT/REJECT/NO_DECISION), the ratio day-cluster bootstrap, the two MUST
guards (winner-NLL non-inferiority, tail calibration-in-the-large), and the codex fixes
(H5 must-before-underpowered, H6 per-arm days, H7 pairing fail-closed, B1 no fail-open).
"""

from __future__ import annotations

import pytest

from horseracing_eval.ev_weight_gate import evaluate_ev_weight_gate


def _row(day, year, rid, hid, p, odds, won):
    return {"race_id": rid, "horse_id": hid, "year": year, "race_day": day,
            "p": p, "odds": odds, "won": won}


def _bet_race(day, year, rid, *, cand_wins=True):
    """Two-horse race, winner=W (odds 2.0). base bets the LOSER (recovery 0); cand bets the
    WINNER (recovery 2.0) when cand_wins, else swapped. Same horse_ids in both arms (paired)."""
    w_base_p, l_base_p = (0.4, 0.6) if cand_wins else (0.6, 0.4)
    w_cand_p, l_cand_p = (0.6, 0.4) if cand_wins else (0.4, 0.6)
    base = [_row(day, year, rid, "W", w_base_p, 2.0, 1), _row(day, year, rid, "L", l_base_p, 2.0, 0)]
    cand = [_row(day, year, rid, "W", w_cand_p, 2.0, 1), _row(day, year, rid, "L", l_cand_p, 2.0, 0)]
    return base, cand


def _dataset(n_days=50, races_per_day=5, *, cand_wins=True):
    base_rows, cand_rows = [], []
    for d in range(n_days):
        day = f"2020-01-{d + 1:02d}"
        year = 2020 + (d % 3)
        for r in range(races_per_day):
            rid = f"{day}-{r}"
            b, c = _bet_race(day, year, rid, cand_wins=cand_wins)
            base_rows += b
            cand_rows += c
    return base_rows, cand_rows


def test_adopt_when_candidate_uniformly_better():
    base_rows, cand_rows = _dataset()
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=500, seed=1)
    assert rep.cand.recovery > rep.base.recovery
    assert rep.delta > 0 and rep.ci_low is not None and rep.ci_low > 0
    assert rep.winner_nll_ok and rep.tail_ok
    assert rep.base.n_bet_days >= 40 and rep.cand.n_bet_days >= 40
    assert rep.verdict == "ADOPT"


def test_reject_when_candidate_worse_ci_upper_below_zero():
    base_rows, cand_rows = _dataset(cand_wins=False)
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=500, seed=1)
    assert rep.delta < 0 and rep.ci_high is not None and rep.ci_high < 0
    assert rep.verdict == "REJECT"


def test_no_decision_when_underpowered():
    base_rows, cand_rows = _dataset(n_days=5, races_per_day=2)
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=200, seed=1)
    assert rep.reasons["enough_power"] is False
    assert rep.verdict == "NO_DECISION"


def test_must_guard_failure_rejects_even_when_underpowered():
    # tiny data (underpowered) + a catastrophic winner-NLL failure -> REJECT wins over
    # NO_DECISION (codex H5). Add no-bet races where cand gives the winner a tiny prob.
    base_rows, cand_rows = _dataset(n_days=4, races_per_day=1)
    for d in range(4):
        day = f"2021-02-{d + 1:02d}"
        rid = f"nll-{d}"
        base_rows += [_row(day, 2021, rid, "A", 0.5, 1.5, 1), _row(day, 2021, rid, "B", 0.5, 1.5, 0)]
        cand_rows += [_row(day, 2021, rid, "A", 0.001, 1.5, 1), _row(day, 2021, rid, "B", 0.999, 1.5, 0)]
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=100, seed=1)
    assert rep.winner_nll_ok is False
    assert rep.reasons["enough_power"] is False  # underpowered...
    assert rep.verdict == "REJECT"                # ...but MUST failure still REJECTs


def test_tail_calibration_guard_failure_rejects():
    # betting races for power/delta; ADD longshot (odds>=21) races where cand over-predicts the
    # tail (calibration-in-the-large up) -> tail guard fails -> REJECT.
    base_rows, cand_rows = _dataset()
    for d in range(30):
        day = f"2022-03-{d + 1:02d}"
        rid = f"tail-{d}"
        base_rows += [_row(day, 2022, rid, "X", 0.01, 30.0, 1), _row(day, 2022, rid, "Y", 0.01, 25.0, 0)]
        cand_rows += [_row(day, 2022, rid, "X", 0.30, 30.0, 1), _row(day, 2022, rid, "Y", 0.30, 25.0, 0)]
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=300, seed=1)
    assert rep.tail_ok is False
    cl_base = rep.tail["base"]["odds_ge_cap"]["calib_large"]
    cl_cand = rep.tail["cand"]["odds_ge_cap"]["calib_large"]
    assert cl_cand > cl_base
    assert rep.verdict == "REJECT"


def test_pairing_mismatch_fails_closed():
    base_rows, cand_rows = _dataset(n_days=3, races_per_day=1)
    cand_rows = cand_rows[:-1]  # drop a horse from candidate only
    with pytest.raises(ValueError, match="populations differ"):
        evaluate_ev_weight_gate(base_rows, cand_rows, b=50, seed=1)


def test_bootstrap_is_deterministic_for_seed():
    base_rows, cand_rows = _dataset()
    a = evaluate_ev_weight_gate(base_rows, cand_rows, b=400, seed=7)
    b = evaluate_ev_weight_gate(base_rows, cand_rows, b=400, seed=7)
    assert (a.ci_low, a.ci_high, a.b_used) == (b.ci_low, b.ci_high, b.b_used)


def test_dead_heat_race_excluded_from_winner_nll():
    # a race with two winners (dead heat) must not contribute to winner NLL
    rows = [
        _row("2020-01-01", 2020, "R", "A", 0.5, 2.0, 1),
        _row("2020-01-01", 2020, "R", "B", 0.5, 2.0, 1),  # second winner
    ]
    rep = evaluate_ev_weight_gate(rows, rows, b=10, seed=1, validate_pairing=True)
    assert rep.base.winner_races == 0  # dead-heat excluded
