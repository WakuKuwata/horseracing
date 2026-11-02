"""176 T023 (SC-004/010): the odds contract of ``update_odds`` / ``update_place_quote`` in the DB."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import Source
from horseracing_db.models import Race, RaceHorse
from sqlalchemy import select, update

from horseracing_scrape.upsert import update_odds, update_place_quote
from tests._seed176 import RID, T1, T2, odds_payload, race_prov, seed_race

pytestmark = pytest.mark.integration
JRA, NK = Source.JRA_OFFICIAL, Source.NETKEIBA
FULL = {1: 2.7, 2: 5.0, 3: 8.1, 4: 21.0}
PLACE = {1: (1.2, 1.6), 2: (1.8, 2.5), 3: (2.0, 3.1), 4: (4.5, 7.0)}


def _state(session):
    session.expire_all()
    race = session.get(Race, RID)
    rows = {
        rh.horse_number: rh
        for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == RID))
    }
    return (
        race,
        {n: rh.odds for n, rh in rows.items()},
        {n: rh.place_odds_low for n, rh in rows.items()},
    )


def _odds(values, official_at, **kw):
    return odds_payload(values, official_at=official_at, **kw)


def test_partial_field_skips_the_whole_race_and_keeps_the_time(session):
    seed_race(session)
    update_odds(session, RID, _odds(FULL, T1), source=NK)
    session.commit()
    c = update_odds(session, RID, _odds({1: 3.0, 2: 4.0}, T2), source=NK)
    session.commit()
    race, odds, _ = _state(session)
    assert c.extra.get("odds_partial_field") == 1 and c.written == 0
    assert race.win_odds_official_at == T1 and odds[1] == Decimal("2.7")


def test_unknown_time_and_backward_time_are_skipped(session):
    seed_race(session)
    update_odds(session, RID, _odds(FULL, T2), source=NK)
    session.commit()
    changed = {**FULL, 1: 3.3}
    c1 = update_odds(session, RID, _odds(changed, None), source=NK)
    c2 = update_odds(session, RID, _odds(changed, T1), source=NK)
    session.commit()
    race, odds, _ = _state(session)
    assert c1.extra.get("odds_skipped_stale") == 1 and c2.extra.get("odds_skipped_stale") == 1
    assert race.win_odds_official_at == T2 and odds[1] == Decimal("2.7")


def test_same_time_same_values_moves_only_the_observation(session):
    seed_race(session)
    update_odds(session, RID, _odds(FULL, T1), source=NK, observed_at=T1)
    session.commit()
    later = T1 + datetime.timedelta(minutes=2)
    c = update_odds(session, RID, _odds(FULL, T1), source=NK, observed_at=later)
    session.commit()
    race, _, _ = _state(session)
    assert c.extra.get("odds_written_races") == 1
    assert race.win_odds_official_at == T1 and race.win_odds_observed_at == later
    assert race_prov(session)["win_odds"] == (NK, T1, later)


def test_same_time_different_values_between_netkeiba_reads_is_written(session):
    seed_race(session)
    update_odds(session, RID, _odds(FULL, T1), source=NK)
    session.commit()
    c = update_odds(session, RID, _odds({**FULL, 1: 2.9}, T1), source=NK)
    session.commit()
    assert c.extra.get("odds_written_races") == 1 and not c.extra.get("odds_conflicts")
    assert _state(session)[1][1] == Decimal("2.9")


def test_official_then_netkeiba_same_time(session):
    seed_race(session)
    update_odds(session, RID, _odds(FULL, T1), source=JRA, observed_at=T1)
    session.commit()
    # same values from netkeiba: written (observation moves) but the source stays official
    later = T1 + datetime.timedelta(minutes=1)
    c = update_odds(session, RID, _odds(FULL, T1), source=NK, observed_at=later)
    session.commit()
    assert c.extra.get("odds_written_races") == 1
    assert race_prov(session)["win_odds"][0] == JRA
    # different values at the same time: official wins, netkeiba is a conflict
    c = update_odds(session, RID, _odds({**FULL, 1: 2.9}, T1), source=NK)
    session.commit()
    assert c.extra.get("odds_conflicts") == 1 and c.written == 0
    assert _state(session)[1][1] == Decimal("2.7")
    assert race_prov(session)["win_odds"][0] == JRA


def test_after_results_only_null_odds_are_filled_and_provenance_is_kept(session):
    seed_race(session, with_result=True, win_odds=Decimal("4.0"))
    session.execute(
        update(RaceHorse)
        .where(RaceHorse.race_id == RID, RaceHorse.horse_number == 2)
        .values(odds=None)
    )
    session.commit()
    before = race_prov(session).get("win_odds")
    c = update_odds(session, RID, _odds(FULL, T2), source=NK)
    session.commit()
    race, odds, _ = _state(session)
    assert odds == {1: Decimal("4.0"), 2: Decimal("5.0"), 3: Decimal("4.0"), 4: Decimal("4.0")}
    assert c.written == 1
    assert race.win_odds_official_at is None and race_prov(session).get("win_odds") == before


def test_place_quote_unknown_time_never_nulls_the_stored_time(session):
    seed_race(session)
    update_place_quote(session, RID, _odds(FULL, T1, place=PLACE), source=NK)
    session.commit()
    c = update_place_quote(
        session, RID, _odds(FULL, None, place={**PLACE, 1: (1.3, 1.7)}), source=NK
    )
    session.commit()
    race, _, place = _state(session)
    assert race.place_odds_official_at == T1
    assert place[1] == Decimal("1.2") and c.extra.get("odds_skipped_stale") == 1
    # a partial place field is skipped as a whole
    c = update_place_quote(session, RID, _odds(FULL, T2, place={1: (1.1, 1.4)}), source=NK)
    session.commit()
    assert (
        c.extra.get("odds_partial_field") == 1 and _state(session)[0].place_odds_official_at == T1
    )
