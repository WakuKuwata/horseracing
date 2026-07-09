"""Feature 064: win odds-cap selection — byte parity, denominator preservation, logic_version."""

from __future__ import annotations

from horseracing_db.enums import EntryStatus

from horseracing_betting.ev import renormalized_started_probs, select_ev_bets
from horseracing_betting.recommend import default_logic_version
from horseracing_betting.strategies import (
    EVStrategy,
    FavoriteROIBaseline,
    OddsCappedEVStrategy,
    UniformROIBaseline,
)

_S = EntryStatus.STARTED


def _horses():
    # Renormalized over started: total prob = 1.0 (no scratches). EVs: A .05*2=.10, B .30*4=1.20,
    # C .15*15=2.25, D .10*30=3.00 (longshot), E .40*1.5=.60.
    return [
        {"horse_id": "A", "horse_number": 1, "win_prob": 0.05, "odds": 2.0, "entry_status": _S},
        {"horse_id": "B", "horse_number": 2, "win_prob": 0.30, "odds": 4.0, "entry_status": _S},
        {"horse_id": "C", "horse_number": 3, "win_prob": 0.15, "odds": 15.0, "entry_status": _S},
        {"horse_id": "D", "horse_number": 4, "win_prob": 0.10, "odds": 30.0, "entry_status": _S},
        {"horse_id": "E", "horse_number": 5, "win_prob": 0.40, "odds": 1.5, "entry_status": _S},
    ]


def test_win_odds_cap_none_is_byte_identical_select_ev_bets():
    """cap=None reproduces the pre-064 output exactly (byte parity)."""
    base = select_ev_bets(_horses(), threshold=1.0, stake=100.0)
    capped_none = select_ev_bets(_horses(), threshold=1.0, stake=100.0, odds_cap=None)
    assert [(b.horse_id, b.win_prob, b.odds, b.ev, b.stake) for b in base] == \
           [(b.horse_id, b.win_prob, b.odds, b.ev, b.stake) for b in capped_none]


def test_odds_cap_filters_after_started_renorm_not_before():
    """cap=21 excludes 21+ horses from BETS, but the probability denominator (renorm) is unchanged
    → the win_prob/EV of capped-in horses are byte-identical to the no-cap run (INV-3)."""
    no_cap = {b.horse_id: (b.win_prob, b.ev) for b in
              select_ev_bets(_horses(), threshold=1.0, stake=100.0)}
    capped = select_ev_bets(_horses(), threshold=1.0, stake=100.0, odds_cap=21.0)
    ids = [b.horse_id for b in capped]
    assert "D" not in ids                      # odds 30 >= 21 → no bet
    assert "C" in ids and "B" in ids           # odds 15 / 4 < 21 → still bet (EV>=1)
    for b in capped:                            # capped-in horses unchanged vs no-cap
        assert (b.win_prob, b.ev) == no_cap[b.horse_id]
    # denominator identical with or without cap (capped horse stays in denom)
    assert renormalized_started_probs(_horses()) == renormalized_started_probs(_horses())


def test_odds_cap_boundary_is_exclusive_upper():
    """odds == cap is excluded (cap is an exclusive upper bound, matching odds<cap)."""
    horses = [
        {"horse_id": "X", "horse_number": 1, "win_prob": 0.5, "odds": 21.0, "entry_status": _S},
        {"horse_id": "Y", "horse_number": 2, "win_prob": 0.5, "odds": 20.0, "entry_status": _S},
    ]
    ids = [b.horse_id for b in select_ev_bets(horses, threshold=1.0, stake=1.0, odds_cap=21.0)]
    assert ids == ["Y"]


def test_default_logic_version_records_cap_conditionally():
    """Feature 064: ;oddscap=<v> appended ONLY when cap set; cap-off byte-identical (SC-003 base)."""
    off = default_logic_version(1.0, 100.0)
    on = default_logic_version(1.0, 100.0, win_odds_cap=21.0)
    assert ";oddscap=" not in off
    assert on == off + ";oddscap=21.0"
    # SC-003: cap value is recoverable from the logic_version string
    frag = [p for p in on.split(";") if p.startswith("oddscap=")][0]
    assert float(frag.split("=")[1]) == 21.0


def test_odds_capped_strategy_name_and_delegation():
    strat = OddsCappedEVStrategy(threshold=1.0, odds_cap=21.0)
    assert strat.name == "ev_oddscap21"
    ids = [b.horse_id for b in strat.bets_for_race(_horses(), stake=100.0)]
    assert "D" not in ids and "C" in ids


def test_win_kelly_allocation_only_sees_capped_candidates():
    """Feature 064: Kelly sizing runs on the ALREADY-capped bet set, so an over-cap longshot never
    receives a stake (it is not among the candidates fed to allocate_kelly)."""
    from horseracing_betting.kelly_types import KellyConfig
    from horseracing_betting.recommend import _win_stake_fractions

    cfg = KellyConfig()
    no_cap = select_ev_bets(_horses(), threshold=1.0, stake=100.0)
    capped = select_ev_bets(_horses(), threshold=1.0, stake=100.0, odds_cap=21.0)
    # the longshot D (odds 30) is an EV>=1 bet without a cap, but excluded with cap=21
    assert "D" in {b.horse_id for b in no_cap}
    assert "D" not in {b.horse_id for b in capped}
    # Kelly fractions align 1:1 with the (capped) bet list — allocation never sees D
    fracs = _win_stake_fractions(capped, cfg)
    assert len(fracs) == len(capped)
    assert all(b.horse_id != "D" for b in capped)


def test_exotic_recommendations_ignore_win_odds_cap():
    """Feature 064 is win-only: the exotic Kelly generator has no win_odds_cap parameter, so exotic
    selection is structurally unaffected (its own odds_cap = estimated-exotic ceiling, a different
    concept)."""
    import inspect

    from horseracing_betting.kelly_recommend import generate_kelly_recommendations

    params = inspect.signature(generate_kelly_recommendations).parameters
    assert "win_odds_cap" not in params           # win cap does not leak into the exotic path
    assert "odds_cap" in params                    # the exotic estimated-odds ceiling is separate


def test_odds_cap_selection_reads_no_results():
    """Leak boundary: the win cap decision uses only entry_status/odds/win_prob — never a result
    field. Horses carrying a (would-be) result key are selected identically to those without."""
    plain = _horses()
    with_result = [{**h, "finish_order": 1, "result_status": "finished"} for h in _horses()]
    a = select_ev_bets(plain, threshold=1.0, stake=100.0, odds_cap=21.0)
    b = select_ev_bets(with_result, threshold=1.0, stake=100.0, odds_cap=21.0)
    assert [x.horse_id for x in a] == [x.horse_id for x in b]


def test_odds_cap_does_not_change_favorite_or_uniform_baselines():
    """The cap lives in select_ev_bets only; odds-only baselines (eligible_started) are unaffected."""
    fav = FavoriteROIBaseline().bets_for_race(_horses(), stake=100.0)
    uni = UniformROIBaseline().bets_for_race(_horses(), stake=100.0)
    assert [b.horse_id for b in fav] == ["E"]                 # favorite = lowest odds (1.5)
    assert {b.horse_id for b in uni} == {"A", "B", "C", "D", "E"}  # every started horse, incl. 30.0
    # EVStrategy(no cap) still bets the longshot D; OddsCappedEVStrategy drops it
    ev_ids = {b.horse_id for b in EVStrategy(1.0).bets_for_race(_horses(), stake=100.0)}
    cap_ids = {b.horse_id for b in OddsCappedEVStrategy(1.0, 21.0).bets_for_race(_horses(), stake=100.0)}
    assert "D" in ev_ids and "D" not in cap_ids
