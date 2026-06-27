"""T004 (019): fail-closed guards (SC-001). DB-backed (no model needed)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import RaceHorse

from horseracing_live import guards
from tests._synth import add_results, seed_pending_race

pytestmark = pytest.mark.integration

_PENDING = "200801019901"


def test_valid_race_id():
    assert guards.valid_race_id("200801019901")[0] is True
    assert guards.valid_race_id("999")[0] is False
    assert guards.valid_race_id("nk:123")[0] is False


def test_result_pending(session):
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1))
    assert guards.is_result_pending(session, _PENDING)[0] is True
    add_results(session, race_id=_PENDING)              # flip to finished
    assert guards.is_result_pending(session, _PENDING)[0] is False


def test_entries_complete(session):
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1))
    assert guards.entries_complete(session, _PENDING)[0] is True
    # missing entries → incomplete
    assert guards.entries_complete(session, "200801019902")[0] is False


def test_odds_present(session):
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1), with_odds=True)
    assert guards.odds_present(session, _PENDING)[0] is True
    # null one horse's odds → not present
    rh = session.query(RaceHorse).filter(
        RaceHorse.race_id == _PENDING, RaceHorse.entry_status == EntryStatus.STARTED
    ).first()
    rh.odds = None
    session.commit()
    assert guards.odds_present(session, _PENDING)[0] is False
