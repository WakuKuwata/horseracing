"""US3 (FR-014): single-win simulation metrics on synthetic data."""

from __future__ import annotations

import datetime

from horseracing_eval.baselines import MarketBaseline
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.operational import BetHorse, simulate_from_predictor, simulate_single_win
from horseracing_eval.predictor import HorseEntry, RaceContext, ResultMarket


def test_all_wins_positive_roi():
    races = [[BetHorse(0.5, 3.0, True), BetHorse(0.3, 2.0, False)] for _ in range(3)]
    m = simulate_single_win(races, threshold=1.0)  # picks H0 (0.5*3=1.5), all win
    assert m.n_bets == 3 and m.hits == 3
    assert m.recovery_rate == 3.0 and m.pseudo_roi == 2.0
    assert m.hit_rate == 1.0 and m.max_consecutive_losses == 0


def test_all_losses_drawdown_and_streak():
    races = [[BetHorse(0.6, 2.0, False)] for _ in range(4)]  # 0.6*2=1.2 >= 1 -> bet, all lose
    m = simulate_single_win(races, threshold=1.0, stake=100)
    assert m.n_bets == 4 and m.hits == 0
    assert m.recovery_rate == 0.0 and m.pseudo_roi == -1.0
    assert m.max_consecutive_losses == 4
    assert m.max_drawdown == 400.0


def test_skip_below_threshold():
    m = simulate_single_win([[BetHorse(0.1, 2.0, False)]], threshold=1.0)  # 0.2 < 1 -> skip
    assert m.n_bets == 0 and m.skip_rate == 1.0


def _informative_eval_race(year: int, seq: int, n: int = 6) -> EvalRace:
    rid = f"{year}0601{seq:02d}01"
    horses = tuple(
        HorseEntry(horse_id=f"{rid}-H{i}", horse_number=i + 1,
                   result_market=ResultMarket(odds=2.0 + 2.0 * i, popularity=None))
        for i in range(n)
    )
    ctx = RaceContext(rid, datetime.date(year, 6, 1), horses)
    labels = tuple(  # favorite (H0, lowest odds) wins
        ScoringLabel(horse_id=h.horse_id, win=int(i == 0), top2=int(i < 2), top3=int(i < 3))
        for i, h in enumerate(horses)
    )
    return EvalRace(context=ctx, labels=labels)


def test_simulate_from_predictor_runs_folds():
    races = [_informative_eval_race(y, s) for y in (2007, 2008) for s in range(1, 4)]
    m = simulate_from_predictor(MarketBaseline(), races)
    assert m.n_races == 3  # only 2008 valid races
    assert m.n_bets >= 0
