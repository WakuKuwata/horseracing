"""US1 (FR-002/003/004/006/013): EV selection — exclude scratch/null-odds, renormalize, no result."""

from __future__ import annotations

from horseracing_db.enums import EntryStatus

from horseracing_betting.ev import renormalized_started_probs, select_ev_bets

_S = EntryStatus.STARTED
_C = EntryStatus.CANCELLED


def _horses():
    # A: started, odds missing -> stays in denominator, no bet
    # B: started, EV=0.333*3=1.0 (< threshold 1.1) -> no bet
    # C: started, EV=0.222*6=1.333 (>= 1.1) -> bet
    # D: cancelled -> excluded from population entirely
    return [
        {"horse_id": "A", "horse_number": 1, "win_prob": 0.40, "odds": None, "entry_status": _S},
        {"horse_id": "B", "horse_number": 2, "win_prob": 0.30, "odds": 3.0, "entry_status": _S},
        {"horse_id": "C", "horse_number": 3, "win_prob": 0.20, "odds": 6.0, "entry_status": _S},
        {"horse_id": "D", "horse_number": 4, "win_prob": 0.10, "odds": 100.0, "entry_status": _C},
    ]


def test_renormalize_over_started_keeps_oddsless_horse():
    probs = renormalized_started_probs(_horses())
    assert set(probs) == {"A", "B", "C"}              # cancelled D excluded
    assert abs(sum(probs.values()) - 1.0) < 1e-9       # renormalized over started (0.9 -> 1.0)
    assert abs(probs["C"] - 0.20 / 0.90) < 1e-9


def test_select_only_ev_above_threshold():
    bets = select_ev_bets(_horses(), threshold=1.1, stake=100.0)
    assert [b.horse_id for b in bets] == ["C"]         # A no odds, B EV=1.0<1.1, D scratched
    c = bets[0]
    assert abs(c.ev - (0.20 / 0.90) * 6.0) < 1e-9
    assert c.stake == 100.0


def test_no_scratch_renormalize_is_noop():
    horses = [
        {"horse_id": "X", "win_prob": 0.6, "odds": 2.0, "entry_status": _S, "horse_number": 1},
        {"horse_id": "Y", "win_prob": 0.4, "odds": 3.0, "entry_status": _S, "horse_number": 2},
    ]
    probs = renormalized_started_probs(horses)
    assert abs(probs["X"] - 0.6) < 1e-9 and abs(probs["Y"] - 0.4) < 1e-9


def test_zero_prob_and_nonpositive_odds_excluded():
    horses = [
        {"horse_id": "Z", "win_prob": 0.0, "odds": 5.0, "entry_status": _S, "horse_number": 1},
        {"horse_id": "W", "win_prob": 0.5, "odds": 0.0, "entry_status": _S, "horse_number": 2},
        {"horse_id": "V", "win_prob": 0.5, "odds": 4.0, "entry_status": _S, "horse_number": 3},
    ]
    bets = select_ev_bets(horses, threshold=1.0, stake=100.0)
    ids = {b.horse_id for b in bets}
    assert "Z" not in ids and "W" not in ids  # zero prob / non-positive odds never bet
