"""Betting the win pool on the exotic pool's opinion: settlement, the null, and the control.

The instrument's whole claim rests on one comparison — does the forward direction beat backing
every started horse? So the null (a pool where the two sides agree) and the blind reference are
tested first, and the demotions that stop a lucky long shot from reading as an edge after that.
"""

from __future__ import annotations

import numpy as np
import pytest

from horseracing_eval.cross_pool_win import (
    THRESHOLDS,
    WIN_PAYOUT_RATE,
    Cell,
    CrossPoolWinRace,
    evaluate,
    holm,
    score_cell,
)


def _race(rid, day, x, q, odds, winner_idx):
    return CrossPoolWinRace(rid, day, np.array(x, float), np.array(q, float),
                            np.array(odds, float), winner_idx)


def _agreeing_pool(n_days=12, races_per_day=8, field=8):
    """x == q, and every horse is priced at the fair-minus-takeout odds. Nothing is selectable
    above R=1, and backing everything returns exactly 1 − takeout."""
    races = []
    idx = 0
    for d in range(n_days):
        for i in range(races_per_day):
            q = [1.0 / field] * field
            odds = [WIN_PAYOUT_RATE * field] * field
            # rotate the winner on a GLOBAL counter so no field position is favoured
            races.append(_race(f"{d}-{i}", f"2026-01-{d + 1:02d}", q, q, odds, idx % field))
            idx += 1
    return races


def _informed_pool(boost=3.0, n_days=12, races_per_day=8, field=8):
    """Same prices, but the exotic side rates the eventual winner above the win pool. This is the
    shape the instrument is meant to detect, and it must show up in the FORWARD direction only."""
    races = []
    idx = 0
    for d in range(n_days):
        for i in range(races_per_day):
            w = idx % field
            q = np.full(field, 1.0 / field)
            x = q.copy()
            x[w] *= boost
            x /= x.sum()
            odds = [WIN_PAYOUT_RATE * field] * field
            races.append(_race(f"{d}-{i}", f"2026-01-{d + 1:02d}", x, q, odds, w))
            idx += 1
    return races


# --- settlement ---------------------------------------------------------------------------------

def test_only_the_winner_pays_and_roi_is_payout_over_stake():
    races = [
        _race("a", "2026-01-01", [0.6, 0.4], [0.3, 0.7], [4.0, 2.0], 0),
        _race("b", "2026-01-02", [0.6, 0.4], [0.3, 0.7], [4.0, 2.0], 1),
    ]
    # x/q = 2.0 for horse 0 in both races, 0.57 for horse 1 → one bet per race
    cell = score_cell(races, direction="exotic_over_win", threshold=1.05, b=50, seed=1)
    assert cell.n_bets == 2 and cell.n_hits == 1
    assert cell.roi == pytest.approx(4.0 / 2)
    assert cell.mean_selected_odds == pytest.approx(4.0)


def test_the_reverse_direction_backs_the_other_side():
    races = [_race("a", "2026-01-01", [0.6, 0.4], [0.3, 0.7], [4.0, 2.0], 0)]
    fwd = score_cell(races, direction="exotic_over_win", threshold=1.05, b=10, seed=1)
    rev = score_cell(races, direction="win_over_exotic", threshold=1.05, b=10, seed=1)
    assert fwd.n_bets == 1 and rev.n_bets == 1
    assert fwd.mean_selected_odds == 4.0 and rev.mean_selected_odds == 2.0


# --- the reference the whole readout hangs on ----------------------------------------------------

def test_backing_everything_returns_one_minus_takeout():
    out = evaluate(_agreeing_pool(), b=200, seed=3)
    assert out["blind_all_started"]["roi"] == pytest.approx(WIN_PAYOUT_RATE, abs=1e-9)
    assert out["reference_return"] == WIN_PAYOUT_RATE


def test_two_pools_that_agree_select_nothing():
    """If the exotic marginal is just the win pool re-expressed there is no disagreement to bet,
    and every threshold cell must be empty rather than quietly backing the whole field."""
    out = evaluate(_agreeing_pool(), b=100, seed=3)
    for c in out["cells"]:
        assert c["n_bets"] == 0, (c["direction"], c["threshold"])
        assert c["verdict"] == "NO_DECISION"
    assert out["holm_survivors"] == []


def test_an_informed_exotic_side_shows_up_forward_and_not_in_reverse():
    """The control's job. A real informational asymmetry must lift the forward direction above the
    blind reference while the reverse direction stays at or below it."""
    races = _informed_pool()
    fwd = score_cell(races, direction="exotic_over_win", threshold=1.05, b=300, seed=3)
    rev = score_cell(races, direction="win_over_exotic", threshold=1.05, b=300, seed=3)
    assert fwd.roi > WIN_PAYOUT_RATE * 1.5
    assert rev.roi == 0.0          # the reverse side never backs the winner in this construction
    assert fwd.verdict == "profitable"


# --- demotions: the guards against a single lucky ticket -----------------------------------------

def test_a_handful_of_hits_is_demoted_however_good_the_ci_looks():
    races = [_race(f"r{i}", f"2026-01-{i % 20 + 1:02d}", [0.9, 0.1], [0.1, 0.9], [50.0, 1.1], 0)
             for i in range(30)]
    cell = score_cell(races, direction="exotic_over_win", threshold=1.05, b=200, seed=1)
    assert cell.n_hits == 30 and cell.verdict == "profitable"

    few = races[:15]
    cell = score_cell(few, direction="exotic_over_win", threshold=1.05, b=200, seed=1)
    assert cell.verdict == "NO_DECISION"
    assert "n_hits" in (cell.demoted_reason or "")


def test_one_ticket_carrying_the_whole_payout_is_demoted():
    races = [_race(f"r{i}", f"2026-01-{i % 20 + 1:02d}", [0.9, 0.1], [0.1, 0.9], [1.0, 1.1], 0)
             for i in range(40)]
    # one enormous winner alongside 39 tiny ones
    races[0] = _race("big", "2026-02-01", [0.9, 0.1], [0.1, 0.9], [10_000.0, 1.1], 0)
    cell = score_cell(races, direction="exotic_over_win", threshold=1.05, b=200, seed=1)
    assert cell.max_single_hit_share > 0.5
    assert cell.verdict == "NO_DECISION"
    assert "carries" in (cell.demoted_reason or "")
    # and the diagnostic shows what the readout would have been without that ticket
    assert cell.leave_one_hit_out_roi < cell.roi


def test_no_bets_is_a_no_decision_not_a_zero_return():
    races = _agreeing_pool(n_days=2, races_per_day=2)
    cell = score_cell(races, direction="exotic_over_win", threshold=1.5, b=10, seed=1)
    assert cell.n_bets == 0 and cell.verdict == "NO_DECISION"
    assert np.isnan(cell.roi)


# --- contract -------------------------------------------------------------------------------------

def test_thresholds_are_frozen():
    """Pre-registered. Adding one after seeing a result is a change of contract, not a tweak."""
    assert THRESHOLDS == (1.05, 1.10, 1.20, 1.50)


def test_holm_covers_the_forward_family_only():
    """The reverse direction is a control; correcting over it would dilute the real family and
    let a control cell be reported as a survivor."""
    def cell(direction, t, p):
        return Cell(direction, t, 1, 1, 30, 1.5, 1.1, 2.0, 20, 0.1, 1.4, p, 5.0, "profitable", None)

    cells = [cell("exotic_over_win", t, 0.001) for t in THRESHOLDS]
    cells += [cell("win_over_exotic", t, 0.001) for t in THRESHOLDS]
    got = holm(cells)
    assert set(got) == {f"T={t}" for t in THRESHOLDS}
    assert all(got.values())


def test_a_demoted_cell_cannot_survive_holm():
    def cell(t, p, demoted=None):
        return Cell("exotic_over_win", t, 1, 1, 5, 1.5, 1.1, 2.0, 20, 0.1, 1.4, p, 5.0,
                    "NO_DECISION" if demoted else "profitable", demoted)

    cells = [cell(1.05, 0.001, "n_hits 5 < 20")] + [cell(t, 0.002) for t in THRESHOLDS[1:]]
    assert holm(cells) == {f"T={t}": False for t in THRESHOLDS}


def test_empty_input_fails_closed():
    with pytest.raises(ValueError, match="no races"):
        evaluate([], b=10, seed=1)
