"""T016 (023 US3): pace_time/position_style ablation separation + market-edge diagnostic (SC-005).

Ablation attributes each 023 group's contribution separately (diagnostic, not the gate). market_edge
reports p−q calibration / edge buckets and states "absolute calibration ≠ market excess".
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.ablation import evaluate_group_ablation
from horseracing_eval.market_edge import evaluate_market_edge
from tests._fakepredictor import FakePredictor, ablation_predictor_factory
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration

_GROUPS = {
    "pace_time": ["rel_last3f_avg", "rel_last3f_best", "rel_time_avg",
                  "finish_diff_avg", "finish_diff_best"],
    "position_style": ["rel_corner_pos_avg", "front_runner_rate", "closer_rate"],
}


def _seed(session):
    for year in (2007, 2008, 2009):
        for r in range(1, 6):
            rid = f"{year}06{r:02d}{r:02d}01"
            insert_race(session, race_id=rid, race_date=datetime.date(year, 6, r),
                        horses=make_informative_field(8, winner=0))


def test_group_contributions_separated(session):
    _seed(session)
    report = evaluate_group_ablation(
        session, make_predictor=ablation_predictor_factory(), groups=_GROUPS,
    )
    assert set(report.group_contribution) == {"pace_time", "position_style"}
    # pace_time is weighted heavier than position_style -> larger contribution, both positive
    assert report.group_contribution["pace_time"] > report.group_contribution["position_style"] > 0


def test_market_edge_diagnostic(session):
    _seed(session)
    r = evaluate_market_edge(session, predictor=FakePredictor(skill=6.0))
    assert r.n_horses > 0
    assert "logloss_p" in r.pq_logloss and "logloss_q" in r.pq_logloss
    assert "≠ market excess" in r.note  # absolute calibration != market excess (SECONDARY)
