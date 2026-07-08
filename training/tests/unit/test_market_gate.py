"""Feature 060: gate driver — odds-coverage restriction + strict q baseline (research D5)."""

from __future__ import annotations

import datetime

import numpy as np
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.predictor import HorseEntry, RaceContext, ResultMarket

from horseracing_training.market_gate import (
    StrictMarketBaseline,
    race_has_full_odds,
    restrict_to_full_odds,
)
from horseracing_training.market_offset import q_from_odds


def _race(rid: str, year: int, odds: list[float | None]) -> EvalRace:
    ctx = RaceContext(
        race_id=rid,
        race_date=datetime.date(year, 6, 1),
        started_horses=tuple(
            HorseEntry(horse_id=f"{rid}_h{i}", result_market=ResultMarket(odds=o, popularity=None))
            for i, o in enumerate(odds)
        ),
    )
    labels = (ScoringLabel(horse_id=f"{rid}_h0", win=1, top2=1, top3=1),)
    return EvalRace(context=ctx, labels=labels)


def test_race_has_full_odds():
    assert race_has_full_odds(_race("A", 2024, [2.0, 4.0]).context)
    assert not race_has_full_odds(_race("B", 2024, [2.0, None]).context)
    assert not race_has_full_odds(_race("C", 2024, [2.0, 0.0]).context)


def test_restrict_to_full_odds_reports_exclusions_by_year():
    races = [
        _race("A", 2023, [2.0, 4.0]),
        _race("B", 2023, [2.0, None]),
        _race("C", 2024, [3.0, 3.0]),
        _race("D", 2024, [None, None]),
        _race("E", 2024, [1.5, 6.0]),
    ]
    kept, report = restrict_to_full_odds(races)
    assert [r.context.race_id for r in kept] == ["A", "C", "E"]
    assert report["n_total_races"] == 5
    assert report["n_kept_races"] == 3
    assert report["n_excluded_races"] == 2
    assert report["excluded_by_year"] == {2023: 1, 2024: 1}


def test_strict_market_baseline_win_equals_q():
    er = _race("A", 2024, [2.0, 4.0, 4.0])
    b = StrictMarketBaseline()
    b.fit([])  # no-op
    preds = b.predict_race(er.context)
    q = q_from_odds([2.0, 4.0, 4.0])
    wins = np.array([preds[h.horse_id].win for h in er.context.started_horses])
    assert np.allclose(wins, q, atol=1e-9)
    assert wins.sum() == 1.0  # renormalized by the shared assemble path


def test_strict_market_baseline_fails_closed_on_missing_odds():
    er = _race("A", 2024, [2.0, None])
    b = StrictMarketBaseline()
    try:
        b.predict_race(er.context)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
