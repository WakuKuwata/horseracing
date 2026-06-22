"""US2 (SC-003/SC-004): walk-forward baseline eval; market beats uniform."""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.baselines import MarketBaseline, UniformBaseline
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration


def _seed(session):
    for year in (2007, 2008, 2009):
        for r in range(1, 6):
            rid = f"{year}06{r:02d}{r:02d}01"  # 12 digits, race_number=01
            insert_race(
                session,
                race_id=rid,
                race_date=datetime.date(year, 6, r),
                horses=make_informative_field(8, winner=0),
            )


def test_market_beats_uniform_on_logloss(session):
    _seed(session)
    races = load_eval_races(session, start_date=datetime.date(2007, 1, 1))
    assert len(races) == 15  # 3 years x 5 races

    market = evaluate(MarketBaseline(), races)
    uniform = evaluate(UniformBaseline(), races)

    assert market.valid_years == [2008, 2009]  # 2007 train-only
    # SC-004: market reference beats the uniform floor on win LogLoss
    assert market.overall["win"]["log_loss"] < uniform.overall["win"]["log_loss"]
    # label-wise metrics present (SC-003)
    for label in ("win", "top2", "top3"):
        assert "log_loss" in market.overall[label]
