"""Bulk load_eval_races must be byte-identical to the former per-race N+1 implementation."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.labels import derive_labels
from horseracing_db.models import Race, RaceHorse
from sqlalchemy import select

from horseracing_eval.dataset import EvalRace, ScoringLabel, load_eval_races
from horseracing_eval.predictor import HorseEntry, RaceContext, ResultMarket
from tests._synth import insert_race

pytestmark = pytest.mark.integration


def _load_eval_races_old(session, start_date=None, end_date=None) -> list[EvalRace]:
    """Verbatim copy of the pre-bulk per-race loader — the equivalence oracle."""
    stmt = select(Race).order_by(Race.race_date, Race.race_id)
    if start_date is not None:
        stmt = stmt.where(Race.race_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(Race.race_date <= end_date)
    out: list[EvalRace] = []
    for race in session.scalars(stmt):
        started = session.scalars(
            select(RaceHorse)
            .where(RaceHorse.race_id == race.race_id)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
            .order_by(RaceHorse.horse_number, RaceHorse.horse_id)
        ).all()
        if not started:
            continue
        horses = tuple(
            HorseEntry(
                horse_id=rh.horse_id, frame=rh.frame, horse_number=rh.horse_number,
                result_market=ResultMarket(
                    odds=float(rh.odds) if rh.odds is not None else None,
                    popularity=rh.popularity,
                ),
            )
            for rh in started
        )
        labels = tuple(
            ScoringLabel(horse_id=r["horse_id"], win=r["win"], top2=r["top2"], top3=r["top3"])
            for r in derive_labels(session, race.race_id)
        )
        if not labels:
            continue
        out.append(EvalRace(
            context=RaceContext(race.race_id, race.race_date, horses), labels=labels))
    return out


def _seed_edge_cases(session):
    d = datetime.date
    # normal 3-horse race
    insert_race(session, race_id="200706010101", race_date=d(2007, 6, 1), horses=[
        {"horse_id": "A", "horse_number": 1, "odds": 2.0, "popularity": 1, "finish_order": 1},
        {"horse_id": "B", "horse_number": 2, "odds": 4.0, "popularity": 2, "finish_order": 2},
        {"horse_id": "C", "horse_number": 3, "odds": 8.0, "popularity": 3, "finish_order": 3},
    ])
    # same day, later race_id + a dead heat for 1st + a DNF (stopped) + a cancelled (excluded)
    insert_race(session, race_id="200706010102", race_date=d(2007, 6, 1), horses=[
        {"horse_id": "D", "horse_number": 1, "odds": 3.0, "finish_order": 1},
        {"horse_id": "E", "horse_number": 2, "odds": 3.0, "finish_order": 1},  # dead heat 1st
        {"horse_id": "F", "horse_number": 3, "odds": 10.0, "finish_order": 0,
         "result_status": "stopped"},  # DNF: result row but not finished
        {"horse_id": "G", "horse_number": 4, "odds": None, "entry_status": EntryStatus.CANCELLED},
    ])
    # null horse_number (NULLS LAST ordering) + missing odds/popularity
    insert_race(session, race_id="200706020101", race_date=d(2007, 6, 2), horses=[
        {"horse_id": "H", "horse_number": None, "finish_order": 2},
        {"horse_id": "I", "horse_number": 1, "odds": 5.0, "finish_order": 1},
    ])
    # a race with NO finishers (all stopped) -> excluded entirely
    insert_race(session, race_id="200706030101", race_date=d(2007, 6, 3), horses=[
        {"horse_id": "J", "horse_number": 1, "finish_order": 0, "result_status": "stopped"},
    ])
    # a race with NO started horses (all cancelled) -> excluded
    insert_race(session, race_id="200706040101", race_date=d(2007, 6, 4), horses=[
        {"horse_id": "K", "horse_number": 1, "entry_status": EntryStatus.CANCELLED},
    ])


def test_bulk_equals_per_race_loader(session):
    _seed_edge_cases(session)
    old = _load_eval_races_old(session)
    new = load_eval_races(session)
    assert new == old  # dataclasses compare deeply — order, horses, labels all byte-identical
    # sanity: the two valid races survived (normal + dead-heat + null-number), edge exclusions gone
    ids = [er.context.race_id for er in new]
    assert ids == ["200706010101", "200706010102", "200706020101"]
    # null horse_number sorts LAST within its race (NULLS LAST parity)
    r3 = next(er for er in new if er.context.race_id == "200706020101")
    assert [h.horse_id for h in r3.context.started_horses] == ["I", "H"]


def test_bulk_equals_per_race_with_date_window(session):
    _seed_edge_cases(session)
    lo, hi = datetime.date(2007, 6, 1), datetime.date(2007, 6, 1)
    assert load_eval_races(session, lo, hi) == _load_eval_races_old(session, lo, hi)
