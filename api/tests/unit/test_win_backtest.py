"""Feature 049: pure WIN backtest logic — hit/miss/void/dead-heat/DNF, win-only. No DB."""

from __future__ import annotations

from horseracing_db.enums import ResultStatus

from horseracing_api.backtest import (
    FavoriteRealized,
    WinRealized,
    favorite_realized,
    win_realized,
)

_WINNER = {"horse_id": "H1", "horse_number": 3}


def _odds_rows(*rows):
    """win_odds-shaped rows: (horse_number, horse_id, odds, updated_at)."""
    return [(hn, hid, o, None) for hn, hid, o in rows]


def test_favorite_is_lowest_odds_and_hits():
    fm = {"H1": (1, ResultStatus.FINISHED), "H2": (2, ResultStatus.FINISHED)}
    fav = favorite_realized(_odds_rows((3, "H1", 2.0), (5, "H2", 6.0)), finish_map=fm, n_winners=1)
    assert fav.horse_number == 3 and fav.odds == 2.0
    assert fav.settled and fav.hit is True and fav.realized_return == 2.0 and fav.realized_roi == 1.0


def test_favorite_miss_is_minus_one():
    fm = {"H1": (3, ResultStatus.FINISHED), "H2": (1, ResultStatus.FINISHED)}
    fav = favorite_realized(_odds_rows((3, "H1", 2.0), (5, "H2", 6.0)), finish_map=fm, n_winners=1)
    assert fav.horse_number == 3 and fav.hit is False and fav.realized_roi == -1.0


def test_favorite_unsettled_all_null():
    fav = favorite_realized(_odds_rows((3, "H1", 2.0)), finish_map={}, n_winners=0)
    assert fav == FavoriteRealized(horse_number=3, odds=2.0, settled=False)


def test_favorite_no_priced_horse_returns_empty():
    fav = favorite_realized([], finish_map={"H1": (1, ResultStatus.FINISHED)}, n_winners=1)
    assert fav == FavoriteRealized()


def _fm(*pairs):
    """finish_map from (horse_id, finish_order, status) triples."""
    return {hid: (fo, st) for hid, fo, st in pairs}


def test_hit_pays_real_odds():
    fm = _fm(("H1", 1, ResultStatus.FINISHED), ("H2", 2, ResultStatus.FINISHED))
    wr = win_realized(_WINNER, 4.5, finish_map=fm, n_winners=1)
    assert wr == WinRealized(settled=True, hit=True, dead_heat=False,
                             realized_return=4.5, realized_roi=3.5)


def test_miss_is_minus_one():
    fm = _fm(("H1", 5, ResultStatus.FINISHED), ("H2", 1, ResultStatus.FINISHED))
    wr = win_realized(_WINNER, 4.5, finish_map=fm, n_winners=1)
    assert wr.settled and wr.hit is False
    assert wr.realized_return == 0.0 and wr.realized_roi == -1.0


def test_dnf_stopped_is_a_loss():
    fm = _fm(("H1", None, ResultStatus.STOPPED), ("H2", 1, ResultStatus.FINISHED))
    wr = win_realized(_WINNER, 4.5, finish_map=fm, n_winners=1)
    assert wr.settled and wr.hit is False and wr.realized_roi == -1.0


def test_disqualified_is_a_loss():
    fm = _fm(("H1", 1, ResultStatus.DISQUALIFIED), ("H2", 1, ResultStatus.FINISHED))
    wr = win_realized(_WINNER, 4.5, finish_map=fm, n_winners=1)
    # DQ is not a FINISHED 1st → loss, even though finish_order==1 is recorded
    assert wr.hit is False and wr.realized_roi == -1.0


def test_dead_heat_hit_flags_split():
    fm = _fm(("H1", 1, ResultStatus.FINISHED), ("H2", 1, ResultStatus.FINISHED))
    wr = win_realized(_WINNER, 4.5, finish_map=fm, n_winners=2)
    assert wr.hit is True and wr.dead_heat is True
    assert wr.realized_return == 4.5  # recorded odds shown; real dividend split (disclosed)


def test_void_when_settled_but_horse_absent():
    fm = _fm(("H2", 1, ResultStatus.FINISHED))  # our horse H1 has no result row
    wr = win_realized(_WINNER, 4.5, finish_map=fm, n_winners=1)
    assert wr.settled is True and wr.hit is None
    assert wr.realized_return is None and wr.realized_roi is None


def test_unsettled_when_no_results():
    wr = win_realized(_WINNER, 4.5, finish_map={}, n_winners=0)
    assert wr == WinRealized()  # all null / settled False


def test_non_win_selection_is_settled_but_unvalued():
    # exotic selection is a list, not a dict → realised valuation is win-only
    fm = _fm(("H1", 1, ResultStatus.FINISHED))
    wr = win_realized([1, 2], None, finish_map=fm, n_winners=1)
    assert wr.settled is True and wr.hit is None and wr.realized_return is None
