"""176 T024: provenance tells the truth per horse and per group (INV-P1..P5)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import Source
from horseracing_db.models import Race

from horseracing_scrape.upsert import update_odds, write_entries
from tests._seed176 import (
    RID,
    T0,
    T1,
    T2,
    entry,
    horse_prov,
    horses,
    odds_payload,
    race_prov,
    race_values,
    seed_race,
)

pytestmark = pytest.mark.integration
JRA, NK = Source.JRA_OFFICIAL, Source.NETKEIBA


def test_only_the_updated_horse_and_group_move(session):
    ids = seed_race(session, n=2)
    write_entries(session, RID, race_values(), [entry(1), entry(2)], source=NK, observed_at=T0)
    session.commit()
    pv = horse_prov(session)
    assert pv[(ids[0], "entry_static")] == (NK, T0) and pv[(ids[1], "entry_static")] == (NK, T0)
    assert (ids[0], "body_weight") not in pv  # nothing weighed yet → no claim made

    # A's body weight comes from the official odds page; B is untouched
    write_entries(
        session,
        RID,
        race_values(),
        [entry(1, weight=480, weight_diff=2), entry(2)],
        source=JRA,
        observed_at=T1,
    )
    session.commit()
    pv = horse_prov(session)
    assert pv[(ids[0], "body_weight")] == (JRA, T1)
    assert (ids[1], "body_weight") not in pv
    # the static group of BOTH horses was re-confirmed by the official page → adoption moves
    assert pv[(ids[0], "entry_static")] == (JRA, T1) and pv[(ids[1], "entry_static")] == (JRA, T1)
    # a rejected input leaves the provenance alone (INV-P5)
    write_entries(
        session,
        RID,
        race_values(),
        [entry(1, weight=470, weight_diff=-8), entry(2)],
        source=NK,
        observed_at=T0,
    )  # older observation than T1
    session.commit()
    assert horse_prov(session)[(ids[0], "body_weight")] == (JRA, T1)
    assert (horses(session)[ids[0]].weight, horses(session)[ids[0]].weight_diff) == (480, 2)


def test_win_odds_provenance_official_at_mirrors_races(session):
    seed_race(session, n=3)
    update_odds(
        session,
        RID,
        odds_payload({1: 2.7, 2: 216.0, 3: 5.1}, official_at=T1),
        source=JRA,
        observed_at=T1,
    )
    session.commit()
    session.expire_all()
    race = session.get(Race, RID)
    rp = race_prov(session)["win_odds"]
    assert rp[0] == JRA and rp[1] == race.win_odds_official_at == T1 and rp[2] == T1
    # a netkeiba quote at a later official time takes over, and the mirror still holds
    update_odds(
        session,
        RID,
        odds_payload({1: 2.8, 2: 210.0, 3: 5.0}, official_at=T2),
        source=NK,
        observed_at=T2,
    )
    session.commit()
    session.expire_all()
    race = session.get(Race, RID)
    rp = race_prov(session)["win_odds"]
    assert rp[0] == NK and rp[1] == race.win_odds_official_at == T2


def test_no_provenance_row_is_invented_for_untouched_races(session):
    seed_race(session, n=2)
    assert horse_prov(session) == {} and race_prov(session) == {}
