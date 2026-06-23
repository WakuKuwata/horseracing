"""US2 (FR-008): EV strategy + ROI baselines select on started+valid-odds, same population."""

from __future__ import annotations

from horseracing_db.enums import EntryStatus

from horseracing_betting.strategies import (
    EVStrategy,
    FavoriteROIBaseline,
    UniformROIBaseline,
)

_S = EntryStatus.STARTED
_C = EntryStatus.CANCELLED


def _horses():
    return [
        {"horse_id": "H1", "horse_number": 1, "win_prob": 0.5, "odds": 2.0, "entry_status": _S},
        {"horse_id": "H2", "horse_number": 2, "win_prob": 0.3, "odds": 5.0, "entry_status": _S},
        {"horse_id": "H3", "horse_number": 3, "win_prob": 0.2, "odds": 10.0, "entry_status": _S},
        {"horse_id": "H4", "horse_number": 4, "win_prob": 0.0, "odds": 1.5, "entry_status": _C},
        {"horse_id": "H5", "horse_number": 5, "win_prob": 0.1, "odds": None, "entry_status": _S},
    ]


def test_ev_strategy_selects_threshold():
    bets = EVStrategy(threshold=1.0).bets_for_race(_horses(), stake=100.0)
    # H5 (started, no odds) STAYS in the probability denominator (codex fix): renorm over
    # H1..H3,H5 (sum=1.1) -> EV H1=0.45*2=0.91 (<1, dropped), H2=0.27*5=1.36, H3=0.18*10=1.82.
    assert {b.horse_id for b in bets} == {"H2", "H3"}


def test_favorite_picks_lowest_odds_started():
    bets = FavoriteROIBaseline().bets_for_race(_horses(), stake=100.0)
    assert len(bets) == 1 and bets[0].horse_id == "H1"  # H4 scratched, H5 no odds


def test_uniform_bets_all_eligible_started():
    bets = UniformROIBaseline().bets_for_race(_horses(), stake=100.0)
    assert {b.horse_id for b in bets} == {"H1", "H2", "H3"}  # excludes H4 (scratch), H5 (no odds)


def test_deterministic():
    h = _horses()
    a = EVStrategy(threshold=1.2).bets_for_race(h, stake=100.0)
    b = EVStrategy(threshold=1.2).bets_for_race(h, stake=100.0)
    assert [(x.horse_id, x.ev) for x in a] == [(x.horse_id, x.ev) for x in b]
