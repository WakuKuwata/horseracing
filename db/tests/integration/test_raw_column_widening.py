"""Feature 055: migration 0010 — nullable raw-column widening (first_3f/prize/owner/breeder/lines)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from horseracing_db.models import Horse, Race, RaceResult

pytestmark = pytest.mark.integration


def test_new_columns_exist_nullable_and_roundtrip(session):
    # all six new columns accept NULL (Unknown ≠ 0) and round-trip values
    session.add(Race(race_id="202601010101", race_number=1,
                     race_date=datetime.date(2026, 1, 1), prize_money=550))
    session.add(Race(race_id="202601010102", race_number=2,
                     race_date=datetime.date(2026, 1, 1)))  # prize NULL ok
    session.add(Horse(horse_id="H055A", horse_name="馬A", owner_name="馬主A",
                      breeder_name="生産者A", sire_line="ロードカナロア系",
                      damsire_line="ネイティヴダンサー系"))
    session.add(Horse(horse_id="H055B", horse_name="馬B"))  # all four NULL ok
    session.flush()
    session.add(RaceResult(race_id="202601010101", horse_id="H055A", finish_order=1,
                           first_3f=Decimal("35.6"), last_3f=Decimal("34.4")))
    session.add(RaceResult(race_id="202601010101", horse_id="H055B", finish_order=2))  # NULL ok
    session.commit()

    r = session.get(Race, "202601010101")
    assert r.prize_money == 550
    assert session.get(Race, "202601010102").prize_money is None
    h = session.get(Horse, "H055A")
    assert (h.owner_name, h.breeder_name) == ("馬主A", "生産者A")
    assert h.sire_line == "ロードカナロア系"
    rr = session.scalars(select(RaceResult).where(RaceResult.horse_id == "H055A")).one()
    assert rr.first_3f == Decimal("35.6")
    assert session.scalars(
        select(RaceResult).where(RaceResult.horse_id == "H055B")).one().first_3f is None
