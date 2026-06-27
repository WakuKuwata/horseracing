"""Feature 020 US3 (SC-008): market-edge diagnostics are SECONDARY, never the adoption gate.

Tests the pure diagnostics (p−q calibration, edge-bucket realized win rate, q-conditional LogLoss)
on hand arrays, the DB collector over synthetic races, and that the report carries the explicit
"better absolute calibration ≠ market excess" disclaimer. pseudo-ROI/Kelly live in betting (011/016),
not here — eval must not import betting (dependency cycle).
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.market_edge import (
    edge_bucket_winrate,
    evaluate_market_edge,
    p_minus_q_summary,
    pq_logloss,
)
from tests._fakepredictor import FakePredictor
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration


def test_p_minus_q_summary():
    s = p_minus_q_summary([0.4, 0.2], [0.3, 0.1], [1, 0])
    assert s["n"] == 2
    assert s["mean_p"] == pytest.approx(0.3)
    assert s["mean_q"] == pytest.approx(0.2)
    assert s["realized"] == pytest.approx(0.5)


def test_edge_bucket_winrate_groups_by_pminusq():
    # two horses with positive edge (win), two with negative edge (loss)
    p = [0.5, 0.5, 0.1, 0.1]
    q = [0.1, 0.1, 0.5, 0.5]
    win = [1, 1, 0, 0]
    buckets = edge_bucket_winrate(p, q, win)
    pos = next(b for b in buckets if b["edge_lo"] >= 0.02)
    neg = next(b for b in buckets if b["edge_hi"] <= -0.02)
    assert pos["n"] == 2 and pos["win_rate"] == pytest.approx(1.0)
    assert neg["n"] == 2 and neg["win_rate"] == pytest.approx(0.0)


def test_pq_logloss_rewards_the_better_probabilities():
    # p concentrates on the actual winner; q is flat -> p must have lower LogLoss
    p = [0.9, 0.05, 0.05]
    q = [0.34, 0.33, 0.33]
    win = [1, 0, 0]
    out = pq_logloss(p, q, win)
    assert out["logloss_p"] < out["logloss_q"]


def test_evaluate_market_edge_over_synthetic_races(session):
    for year in (2007, 2008, 2009):
        for r in range(1, 5):
            rid = f"{year}06{r:02d}{r:02d}01"
            insert_race(
                session, race_id=rid, race_date=datetime.date(year, 6, r),
                horses=make_informative_field(8, winner=0),
            )
    report = evaluate_market_edge(session, predictor=FakePredictor(skill=6.0))
    assert report.n_horses > 0
    assert "mean_p" in report.summary and "mean_q" in report.summary
    assert "logloss_p" in report.pq_logloss and "logloss_q" in report.pq_logloss
    assert len(report.edge_buckets) >= 1
    # SC-008: the disclaimer that absolute-calibration gains are NOT market excess is explicit,
    # and pseudo-ROI/Kelly are flagged SECONDARY (run in betting, not gated here).
    assert "≠ market excess" in report.note
    assert "SECONDARY" in report.note
