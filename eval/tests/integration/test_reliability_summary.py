"""T014 (US2 integration): evaluate() emits walk-forward OOS reliability into the summary.

The reliability lives under to_summary()['eval']['reliability'][label] = {bins, n_total} and is OOS by
construction (pooled valid folds), so the API can read it from metrics_summary without recompute.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate
from tests._fakepredictor import FakePredictor
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration


def _seed(session):
    for year in (2007, 2008, 2009):
        for r in range(1, 6):
            rid = f"{year}06{r:02d}{r:02d}01"
            insert_race(session, race_id=rid, race_date=datetime.date(year, 6, r),
                        horses=make_informative_field(8, winner=0))


def test_summary_includes_reliability(session):
    _seed(session)
    races = load_eval_races(session, start_date=datetime.date(2007, 1, 1))
    result = evaluate(FakePredictor(skill=8.0), races)

    summary = result.to_summary()["eval"]
    assert "reliability" in summary
    win = summary["reliability"]["win"]
    assert win["n_total"] > 0
    assert win["bins"], "reliability bins present"
    b = win["bins"][0]
    for key in ("pred_lo", "pred_hi", "pred_mean", "realized_rate",
                "realized_ci_low", "realized_ci_high", "count", "suppressed"):
        assert key in b
    # bins reflect only OOS valid years (2008, 2009 -> 2007 train-only)
    assert result.valid_years == [2008, 2009]
