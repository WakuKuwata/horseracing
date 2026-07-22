"""Feature 079 (step 4): paired EV-weight adoption gate — pure scorer.

Locks the verdict rule (ADOPT/REJECT/NO_DECISION), the ratio day-cluster bootstrap, and the two
MUST guards (winner-NLL non-inferiority, tail over-prediction) BEFORE the run. Rows are synthetic;
these are logic guards, not accuracy claims.
"""

from __future__ import annotations

from horseracing_eval.ev_weight_gate import evaluate_ev_weight_gate


def _row(day, year, rid, p, odds, won):
    return {"race_id": rid, "year": year, "race_day": day, "p": p, "odds": odds, "won": won}


def _bet_race(day, year, rid, *, cand_wins=True):
    """Two-horse race, winner=W (odds 2.0). base bets the LOSER (recovery 0); cand bets the
    WINNER (recovery 2.0) when cand_wins, else the arms are swapped."""
    w_base_p, l_base_p = 0.4, 0.6   # base: EV_L=1.2 bets L(lose); EV_W=0.8 no bet -> recovery 0
    w_cand_p, l_cand_p = 0.6, 0.4   # cand: EV_W=1.2 bets W(win) -> recovery 2.0
    if not cand_wins:
        w_base_p, w_cand_p = w_cand_p, w_base_p
        l_base_p, l_cand_p = l_cand_p, l_base_p
    base = [_row(day, year, rid, w_base_p, 2.0, 1), _row(day, year, rid, l_base_p, 2.0, 0)]
    cand = [_row(day, year, rid, w_cand_p, 2.0, 1), _row(day, year, rid, l_cand_p, 2.0, 0)]
    return base, cand


def _dataset(n_days=50, races_per_day=5, *, cand_wins=True):
    base_rows, cand_rows = [], []
    for d in range(n_days):
        day = f"2020-01-{d + 1:02d}"
        year = 2020 + (d % 3)  # spread across 3 year-folds
        for r in range(races_per_day):
            rid = f"{day}-{r}"
            b, c = _bet_race(day, year, rid, cand_wins=cand_wins)
            base_rows += b
            cand_rows += c
    return base_rows, cand_rows


def test_adopt_when_candidate_uniformly_better():
    base_rows, cand_rows = _dataset()  # cand bets winners every race
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=500, seed=1)
    assert rep.cand.recovery > rep.base.recovery
    assert rep.delta > 0 and rep.ci_low is not None and rep.ci_low > 0
    assert rep.winner_nll_ok and rep.tail_ok
    assert rep.verdict == "ADOPT"


def test_reject_when_candidate_worse_ci_upper_below_zero():
    base_rows, cand_rows = _dataset(cand_wins=False)  # cand bets losers
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=500, seed=1)
    assert rep.delta < 0 and rep.ci_high is not None and rep.ci_high < 0
    assert rep.verdict == "REJECT"


def test_no_decision_when_underpowered():
    # few days / few bets -> below MIN_BETS/MIN_DAYS -> NO_DECISION regardless of delta
    base_rows, cand_rows = _dataset(n_days=5, races_per_day=2)
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=200, seed=1)
    assert rep.reasons["underpowered"] is True
    assert rep.verdict == "NO_DECISION"


def test_winner_nll_guard_failure_rejects():
    # betting races give cand a positive recovery delta + power; ADD no-bet races where cand
    # assigns the winner a tiny prob -> cand winner NLL >> base + tol -> MUST guard fails -> REJECT
    base_rows, cand_rows = _dataset()
    for d in range(50):
        day = f"2021-02-{d + 1:02d}"
        rid = f"nll-{d}"
        # odds 1.5 -> EV<1 both horses -> no bet; winner=first horse
        base_rows += [_row(day, 2021, rid, 0.5, 1.5, 1), _row(day, 2021, rid, 0.5, 1.5, 0)]
        cand_rows += [_row(day, 2021, rid, 0.01, 1.5, 1), _row(day, 2021, rid, 0.99, 1.5, 0)]
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=300, seed=1)
    assert rep.winner_nll_ok is False
    assert rep.verdict == "REJECT"


def test_tail_over_prediction_guard_failure_rejects():
    # betting races for power/delta; ADD longshot (odds>=21) races where cand massively
    # over-predicts the tail (E/O up) -> tail guard fails -> REJECT. Longshots are cap-excluded
    # so they never bet and do not move recovery.
    base_rows, cand_rows = _dataset()
    for d in range(30):
        day = f"2022-03-{d + 1:02d}"
        rid = f"tail-{d}"
        # one tail winner (won=1) so O>0; base under-predicts, cand over-predicts
        base_rows += [_row(day, 2022, rid, 0.01, 30.0, 1), _row(day, 2022, rid, 0.01, 25.0, 0)]
        cand_rows += [_row(day, 2022, rid, 0.30, 30.0, 1), _row(day, 2022, rid, 0.30, 25.0, 0)]
    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=300, seed=1)
    assert rep.tail_ok is False
    assert rep.tail["cand"]["odds_ge_cap"]["ratio"] > rep.tail["base"]["odds_ge_cap"]["ratio"]
    assert rep.verdict == "REJECT"


def test_bootstrap_is_deterministic_for_seed():
    base_rows, cand_rows = _dataset()
    a = evaluate_ev_weight_gate(base_rows, cand_rows, b=400, seed=7)
    b = evaluate_ev_weight_gate(base_rows, cand_rows, b=400, seed=7)
    assert (a.ci_low, a.ci_high) == (b.ci_low, b.ci_high)
