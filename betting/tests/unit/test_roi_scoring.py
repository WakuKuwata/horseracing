"""US2 (FR-007/009): pseudo-ROI scoring — win/loss/DNF/scratch/dead-heat, race-level DD/streak."""

from __future__ import annotations

from horseracing_betting.ev import Bet
from horseracing_betting.roi import RaceOutcome, score_backtest


class _StubStrategy:
    """Returns predetermined bets per race_id (lets us control the scoring inputs exactly)."""

    name = "stub"

    def __init__(self, by_race: dict[str, list[Bet]]):
        self._by_race = by_race

    def bets_for_race(self, horses, *, stake):
        return self._by_race.get(horses[0]["race_id"], []) if horses else []


def _bet(hid, odds, stake=100.0):
    return Bet(horse_id=hid, horse_number=None, win_prob=None, odds=odds, ev=None, stake=stake)


def test_scoring_win_loss_dnf_skip_and_streak():
    outcomes = [
        # race1: 2 bets, H1 wins (odds 1.5), H2 loses -> race_pnl = 50 - 100 = -50
        RaceOutcome("r1", [{"race_id": "r1"}], winners={"H1"}),
        # race2: no bet -> skip
        RaceOutcome("r2", [{"race_id": "r2"}], winners={"Z"}),
        # race3: DNF (no winner), bet loses -> race_pnl = -100
        RaceOutcome("r3", [{"race_id": "r3"}], winners=set()),
    ]
    strat = _StubStrategy({
        "r1": [_bet("H1", 1.5), _bet("H2", 4.0)],
        "r2": [],
        "r3": [_bet("H3", 3.0)],
    })
    r = score_backtest(outcomes, strat, stake=100.0)

    assert r.n_races == 3 and r.n_bet_races == 2 and r.n_bets == 3
    assert r.total_stake == 300.0 and r.total_payout == 150.0
    assert abs(r.recovery_rate - 0.5) < 1e-9
    assert abs(r.hit_rate - 1 / 3) < 1e-9
    assert abs(r.skip_rate - 1 / 3) < 1e-9
    assert r.max_drawdown == 150.0          # cum: -50, -150 -> peak 0, max DD 150 (absolute)
    assert r.max_losing_streak == 2         # r1, r3 both losing bet races (r2 skip not counted)
    assert r.pseudo is True


def test_dead_heat_first_counts_as_hit():
    outcomes = [RaceOutcome("r1", [{"race_id": "r1"}], winners={"H1", "H2"})]
    strat = _StubStrategy({"r1": [_bet("H2", 2.0)]})
    r = score_backtest(outcomes, strat, stake=100.0)
    assert r.hit_rate == 1.0 and r.total_payout == 200.0
