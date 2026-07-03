"""Feature 055: new raw columns populate on (re-)ingest, idempotent, existing values untouched."""

from __future__ import annotations

from decimal import Decimal

import pytest
from horseracing_db.models import Horse, Race, RaceResult
from sqlalchemy import select

from horseracing_ingest.pipeline import ingest_year
from tests._sjis import make_row, write_csv

pytestmark = pytest.mark.integration

RACE_ID = "200701010101"


def _fixture(tmp_path):
    rows = [
        make_row(horse_id="H1", horse_number="1", finish_order="1",
                 first_3f="35.6", owner_name="馬主A", breeder_name="牧場A",
                 prize_money="550"),
        make_row(horse_id="H2", horse_number="2", finish_order="2",
                 first_3f="", owner_name="", breeder_name="",
                 sire_line="", damsire_line=""),  # missing -> NULL
    ]
    return write_csv(tmp_path / "2007", rows)


def _snapshot(session):
    """All rows/values of the three widened tables, deterministically ordered."""
    races = [(r.race_id, r.prize_money, r.distance, r.race_class)
             for r in session.scalars(select(Race).order_by(Race.race_id))]
    horses = [(h.horse_id, h.owner_name, h.breeder_name, h.sire_line, h.damsire_line,
               h.sire_name)
              for h in session.scalars(select(Horse).order_by(Horse.horse_id))]
    results = [(rr.race_id, rr.horse_id, rr.first_3f, rr.last_3f, rr.finish_order)
               for rr in session.scalars(
                   select(RaceResult).order_by(RaceResult.race_id, RaceResult.horse_id))]
    return races, horses, results


def test_new_columns_populate_and_idempotent(session, tmp_path):
    p = _fixture(tmp_path)
    ingest_year(session, p)
    snap1 = _snapshot(session)

    race = session.get(Race, RACE_ID)
    assert race.prize_money == 550
    h1 = session.get(Horse, "H1")
    assert (h1.owner_name, h1.breeder_name) == ("馬主A", "牧場A")
    assert session.get(RaceResult, (RACE_ID, "H1")).first_3f == Decimal("35.6")
    # missing -> NULL (Unknown != 0)
    h2 = session.get(Horse, "H2")
    assert h2.owner_name is None and h2.sire_line is None
    assert session.get(RaceResult, (RACE_ID, "H2")).first_3f is None

    ingest_year(session, p)  # re-run: full-value idempotency (existing columns included)
    assert _snapshot(session) == snap1


def test_reingest_backfills_new_columns_without_touching_existing(session, tmp_path):
    """Simulates the production backfill: rows ingested BEFORE the widening (new cols NULL)
    get populated by a re-run while every pre-existing value stays identical."""
    p = _fixture(tmp_path)
    ingest_year(session, p)
    # simulate the pre-055 state: blank out the new columns only
    race = session.get(Race, RACE_ID)
    race.prize_money = None
    h1 = session.get(Horse, "H1")
    h1.owner_name = h1.breeder_name = h1.sire_line = h1.damsire_line = None
    rr1 = session.get(RaceResult, (RACE_ID, "H1"))
    rr1.first_3f = None
    session.commit()
    before_races, before_horses, before_results = _snapshot(session)

    ingest_year(session, p)  # the backfill
    session.expire_all()  # raw-SQL upserts bypass the ORM identity map

    after_races, after_horses, after_results = _snapshot(session)
    # new columns repopulated
    assert session.get(Race, RACE_ID).prize_money == 550
    assert session.get(Horse, "H1").owner_name == "馬主A"
    assert session.get(RaceResult, (RACE_ID, "H1")).first_3f == Decimal("35.6")
    # existing (non-055) values byte-identical: compare the snapshot minus the new columns
    strip_r = [(rid, d, c) for (rid, _p, d, c) in before_races]
    strip_r2 = [(rid, d, c) for (rid, _p, d, c) in after_races]
    assert strip_r == strip_r2
    assert [(h[0], h[5]) for h in before_horses] == [(h[0], h[5]) for h in after_horses]
    assert [(r[0], r[1], r[3], r[4]) for r in before_results] == \
           [(r[0], r[1], r[3], r[4]) for r in after_results]
