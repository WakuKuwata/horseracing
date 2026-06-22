"""US4 (FR-015): compare two stored predictors at the same conditions."""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.baselines import MarketBaseline, UniformBaseline
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate
from horseracing_eval.report import compare
from horseracing_eval.store import save_baseline
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration


def test_compare_two_baselines(session):
    for year in (2007, 2008, 2009):
        for r in range(1, 4):
            insert_race(
                session,
                race_id=f"{year}06{r:02d}{r:02d}01",
                race_date=datetime.date(year, 6, r),
                horses=make_informative_field(8, winner=0),
            )
    races = load_eval_races(session, start_date=datetime.date(2007, 1, 1))
    save_baseline(session, "baseline-market-v1", evaluate(MarketBaseline(), races))
    save_baseline(session, "baseline-uniform-v1", evaluate(UniformBaseline(), races))

    cmp = compare(session, "baseline-market-v1", "baseline-uniform-v1")
    assert cmp.same_scheme is True
    # market better than uniform -> (market - uniform) win LogLoss is negative
    assert cmp.diffs["win"]["log_loss"] is not None
    assert cmp.diffs["win"]["log_loss"] < 0
