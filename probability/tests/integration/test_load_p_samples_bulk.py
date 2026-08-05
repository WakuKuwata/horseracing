"""Bulk load_p_samples parity (interactive-latency fix): the 4-query bulk loader must be
value-identical to the per-race ``_latest_run_predictions``/``_winner`` reference on every edge
(latest-run selection, dead heat, null win_prob, non-started horses, prediction-less races)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import (
    Horse,
    PredictionRun,
    Race,
    RaceHorse,
    RacePrediction,
    RaceResult,
)

from horseracing_probability.model_calibration import (
    _latest_run_predictions,
    _winner,
    load_p_samples,
)
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration

D1 = datetime.date(2008, 6, 1)
D2 = datetime.date(2008, 6, 2)


def _reference(session, *, date_from, date_to):
    """The pre-bulk per-race implementation, kept verbatim as the parity oracle."""
    from sqlalchemy import select

    rows = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .order_by(Race.race_date, Race.race_id)
    ).all()
    out = []
    for race_id, race_date in rows:
        p = _latest_run_predictions(session, race_id)
        winner, dead_heat = _winner(session, race_id)
        out.append((race_id, race_date, p, winner, dead_heat))
    return out


def _assert_parity(session, *, date_from=D1, date_to=D2):
    ref = _reference(session, date_from=date_from, date_to=date_to)
    bulk = load_p_samples(session, date_from=date_from, date_to=date_to)
    assert len(bulk) == len(ref)
    for (rid_b, d_b, p_b, w_b, dh_b), (rid_r, d_r, p_r, w_r, dh_r) in zip(bulk, ref,
                                                                          strict=True):
        assert rid_b == rid_r and d_b == d_r
        assert p_b == p_r, f"p mismatch for {rid_r}"
        assert w_b == w_r and dh_b == dh_r, f"winner mismatch for {rid_r}"
    return bulk


def test_parity_basic_and_dead_heat_and_empty(session):
    seed_predicted_race(session, race_id="200806010101",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3}, race_date=D1)
    # dead heat at 1st -> winner None + flag
    seed_predicted_race(session, race_id="200806010102",
                        win_probs={"H1": 0.4, "H2": 0.4, "H3": 0.2},
                        finish={"H1": 1, "H2": 1, "H3": 3}, race_date=D1)
    # race with NO predictions and NO results (result-pending, never predicted)
    session.merge(Race(race_id="200806020101", race_number=1, race_date=D2, venue_code="06"))
    session.commit()

    bulk = _assert_parity(session)
    assert [r[0] for r in bulk] == ["200806010101", "200806010102", "200806020101"]
    assert bulk[1][3] is None and bulk[1][4] is True   # dead heat
    assert bulk[2][2] == {} and bulk[2][3] is None and bulk[2][4] is False


def test_parity_latest_run_and_filters(session):
    # two runs for one race: the LATER computed_at run must win in both paths
    seed_predicted_race(session, race_id="200806010103",
                        win_probs={"H1": 0.6, "H2": 0.4},
                        finish={"H1": 1, "H2": 2}, race_date=D1)
    run2 = PredictionRun(race_id="200806010103", model_version="m1", logic_version="v2")
    session.add(run2)
    session.flush()
    for hid, wp in (("H1", "0.55"), ("H2", "0.45")):
        from decimal import Decimal
        session.add(RacePrediction(prediction_run_id=run2.prediction_run_id, horse_id=hid,
                                   win_prob=Decimal(wp)))
    # a non-started (cancelled) horse WITH a prediction row + a null-prob started horse
    session.merge(Horse(horse_id="HC", horse_name="HC"))
    session.merge(Horse(horse_id="HN", horse_name="HN"))
    session.flush()
    session.add(RaceHorse(race_id="200806010103", horse_id="HC",
                          entry_status=EntryStatus.CANCELLED))
    session.add(RaceHorse(race_id="200806010103", horse_id="HN",
                          entry_status=EntryStatus.STARTED))
    session.add(RacePrediction(prediction_run_id=run2.prediction_run_id, horse_id="HC",
                               win_prob=0.99))
    session.add(RacePrediction(prediction_run_id=run2.prediction_run_id, horse_id="HN",
                               win_prob=None))
    # a DNF result row that must not create a winner
    session.add(RaceResult(race_id="200806010103", horse_id="HN", finish_order=None,
                           result_status=ResultStatus.STOPPED))
    session.commit()

    bulk = _assert_parity(session)
    row = next(r for r in bulk if r[0] == "200806010103")
    assert row[2] == {"H1": 0.55, "H2": 0.45}  # latest run; HC (cancelled) + HN (null) excluded
    assert row[3] == "H1" and row[4] is False


def test_parity_window_excludes_out_of_range(session):
    seed_predicted_race(session, race_id="200806010104",
                        win_probs={"H1": 0.5, "H2": 0.5}, finish={"H1": 1, "H2": 2}, race_date=D1)
    seed_predicted_race(session, race_id="200806020102",
                        win_probs={"H1": 0.5, "H2": 0.5}, finish={"H1": 1, "H2": 2}, race_date=D2)
    bulk = _assert_parity(session, date_from=D1, date_to=D1)
    assert [r[0] for r in bulk] == ["200806010104"]
