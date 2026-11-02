"""176 T057: the official 単複 page → update_odds under the shared contract (SC-004)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import Source
from horseracing_db.models import Race, RaceHorse
from sqlalchemy import select

from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.parse.odds import parse_win_odds
from horseracing_scrape.jra.upsert_jra import popularity_ranks, upsert_jra_entries, upsert_jra_odds
from horseracing_scrape.upsert import update_odds
from tests._seed176 import odds_payload, race_prov
from tests.jra_fixtures import JST, fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
RID = "202605041111"
JRA, NK = Source.JRA_OFFICIAL, Source.NETKEIBA
T0822 = datetime.datetime(2026, 11, 1, 8, 22, tzinfo=JST)


def _seed_entries(session):
    page = parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)
    changes = parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)
    upsert_jra_entries(
        session,
        page,
        changes,
        observed_at=fetched_at("entries_tokyo_20261101.sjis.html"),
        race_ids=[RID],
    )
    session.commit()


def _odds():
    return parse_win_odds(jra_text("odds_tokyo11r_20261101_0822.sjis.html"), expected_date=D)


def _stored(session):
    session.expire_all()
    race = session.get(Race, RID)
    rows = {
        rh.horse_number: rh
        for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == RID))
    }
    return race, rows


def test_official_odds_written_with_page_time_and_rank_popularity(session):
    _seed_entries(session)
    obs = datetime.datetime(2026, 11, 1, 8, 25, tzinfo=JST)
    c = upsert_jra_odds(session, _odds(), observed_at=obs)
    session.commit()
    race, rows = _stored(session)
    assert c.extra.get("odds_written_races") == 1
    assert race.win_odds_official_at == T0822 and race.win_odds_observed_at == obs
    assert rows[1].odds == Decimal("2.7") and rows[1].popularity == 1
    assert rows[2].odds == Decimal("216.0")
    assert (rows[1].place_odds_low, rows[1].place_odds_high) == (Decimal("1.2"), Decimal("1.6"))
    assert race.place_odds_official_at == T0822
    pv = race_prov(session, RID)
    assert pv["win_odds"] == (JRA, T0822, obs) and pv["place_odds"][0] == JRA
    # popularity is the rank by odds (ties share the lower rank)
    assert popularity_ranks({1: 2.7, 2: 216.0, 3: 2.7}) == {1: 1, 3: 1, 2: 3}


def test_same_page_again_changes_no_value_only_observation(session):
    _seed_entries(session)
    obs1 = datetime.datetime(2026, 11, 1, 8, 25, tzinfo=JST)
    obs2 = obs1 + datetime.timedelta(minutes=3)
    upsert_jra_odds(session, _odds(), observed_at=obs1)
    session.commit()
    race1, rows1 = _stored(session)
    obs_before = race1.win_odds_observed_at
    before = {n: (rh.odds, rh.popularity, rh.place_odds_low) for n, rh in rows1.items()}
    upsert_jra_odds(session, _odds(), observed_at=obs2)
    session.commit()
    race2, rows2 = _stored(session)
    assert {n: (rh.odds, rh.popularity, rh.place_odds_low) for n, rh in rows2.items()} == before
    assert race2.win_odds_official_at == race1.win_odds_official_at == T0822
    assert race2.win_odds_observed_at == obs2 > obs_before


def test_netkeiba_same_time_different_value_is_skipped_newer_wins(session):
    _seed_entries(session)
    upsert_jra_odds(session, _odds(), observed_at=datetime.datetime(2026, 11, 1, 8, 25, tzinfo=JST))
    session.commit()
    _, rows = _stored(session)
    started = sorted(n for n, rh in rows.items() if rh.entry_status == "started")
    conflicting = {n: float(rows[n].odds) for n in started}
    conflicting[1] = 2.9
    c = update_odds(session, RID, odds_payload(conflicting, official_at=T0822), source=NK)
    session.commit()
    assert c.extra.get("odds_conflicts") == 1 and c.written == 0
    assert _stored(session)[1][1].odds == Decimal("2.7")
    newer = update_odds(
        session,
        RID,
        odds_payload(conflicting, official_at=T0822 + datetime.timedelta(minutes=8)),
        source=NK,
    )
    session.commit()
    assert newer.extra.get("odds_written_races") == 1
    assert _stored(session)[1][1].odds == Decimal("2.9")
    assert race_prov(session, RID)["win_odds"][0] == NK


def test_prev_day_final_is_ignored(session):
    _seed_entries(session)
    html = jra_text("odds_tokyo11r_20261101_0822.sjis.html").replace(
        "8時22分現在オッズ", "前日最終オッズ"
    )
    c = upsert_jra_odds(
        session,
        parse_win_odds(html, expected_date=D),
        observed_at=datetime.datetime(2026, 11, 1, 8, 25, tzinfo=JST),
    )
    session.commit()
    assert c.extra.get("odds_prev_day_final_ignored") == 1
    assert _stored(session)[0].win_odds_official_at is None


def test_odds_page_id_mismatch_is_quarantined(session):
    _seed_entries(session)
    html = jra_text("odds_tokyo11r_20261101_0822.sjis.html").replace(
        "pw01dud002022105102/98", "pw01dud002022105199/98"
    )
    c = upsert_jra_odds(
        session,
        parse_win_odds(html, expected_date=D),
        observed_at=datetime.datetime(2026, 11, 1, 8, 25, tzinfo=JST),
    )
    session.commit()
    assert c.extra.get("quarantined_collision") == 1 and c.written == 0
    assert _stored(session)[0].win_odds_official_at is None
