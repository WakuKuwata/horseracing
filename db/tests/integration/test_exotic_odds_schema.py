"""T004 (012): exotic_odds single-latest-value overwrite, UNIQUE, CHECK constraints (SC-001/003)."""

from __future__ import annotations

import datetime
import time

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from horseracing_db.models import ExoticOdds, Race

pytestmark = pytest.mark.integration

RACE_ID = "202705021101"


def _race(session):
    session.add(Race(race_id=RACE_ID, race_number=11, race_date=datetime.date(2027, 5, 1)))
    session.commit()


def test_insert_and_overwrite_keeps_single_row(session):
    _race(session)
    row = ExoticOdds(race_id=RACE_ID, bet_type="exacta", selection=[7, 3], odds=12.3,
                     coverage_scope="full", source="netkeiba")
    session.add(row)
    session.commit()
    before = row.updated_at

    # ON CONFLICT overwrite (pre-race -> final dividend), latest value, NO history row
    time.sleep(0.05)
    stmt = insert(ExoticOdds).values(
        race_id=RACE_ID, bet_type="exacta", selection=[7, 3], odds=18.9, coverage_scope="full",
        source="netkeiba",
    ).on_conflict_do_update(
        index_elements=["race_id", "bet_type", "selection"],
        set_={"odds": 18.9, "updated_at": datetime.datetime.now(datetime.UTC)},
    )
    session.execute(stmt)
    session.commit()
    session.expire_all()  # raw ON CONFLICT bypassed the identity map

    rows = session.execute(
        select(ExoticOdds).where(ExoticOdds.race_id == RACE_ID, ExoticOdds.bet_type == "exacta")
    ).scalars().all()
    assert len(rows) == 1, "overwrite must not create a history row (constitution V)"
    assert float(rows[0].odds) == 18.9
    assert rows[0].updated_at > before


def test_unique_constraint_on_race_bettype_selection(session):
    _race(session)
    session.add(ExoticOdds(race_id=RACE_ID, bet_type="trio", selection=[1, 3, 7], odds=5.0))
    session.commit()
    session.add(ExoticOdds(race_id=RACE_ID, bet_type="trio", selection=[1, 3, 7], odds=6.0))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_bet_type_check_rejects_win(session):
    _race(session)
    session.add(ExoticOdds(race_id=RACE_ID, bet_type="win", selection=[1], odds=2.0))
    with pytest.raises(IntegrityError):  # win has no exotic pool (EXOTIC_BET_TYPE check)
        session.commit()
    session.rollback()


def test_coverage_scope_check_rejects_unknown(session):
    _race(session)
    session.add(ExoticOdds(race_id=RACE_ID, bet_type="place", selection=[5], odds=2.0,
                           coverage_scope="bogus"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_different_selection_is_distinct_row(session):
    _race(session)
    session.add(ExoticOdds(race_id=RACE_ID, bet_type="exacta", selection=[7, 3], odds=12.3))
    session.add(ExoticOdds(race_id=RACE_ID, bet_type="exacta", selection=[3, 7], odds=20.1))
    session.commit()
    rows = session.scalars(
        select(ExoticOdds).where(ExoticOdds.race_id == RACE_ID, ExoticOdds.bet_type == "exacta")
    ).all()
    assert len(rows) == 2  # ordered exacta [7,3] != [3,7]
