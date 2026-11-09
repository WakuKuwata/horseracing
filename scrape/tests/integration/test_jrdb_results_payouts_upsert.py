"""178 T044: SED 正式版 → race_results (race-atomic) + 確定単勝オッズ (value-changing rows only) +
HJC payouts consistent with it; SRB → race_laps(source=jrdb); the 速報版 writes nothing; TYB
body-weight pair and one-way 取消."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.models import ExoticOdds, OfficialWinPayout, RaceHorse, RaceLaps, RaceResult
from sqlalchemy import func, select, text

from horseracing_scrape.jrdb import upsert_jrdb as W
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from tests._jrdb_synth import (
    JST,
    YEAR,
    decode_public,
    entries_from_results,
    horses_from_results,
    payouts_from_results,
    prerace_from_results,
    shifted_programs,
    shifted_results,
)

pytestmark = pytest.mark.integration
OBS = datetime.datetime(2026, 9, 17, 16, 30, tzinfo=JST)


def _field(session, results):
    st = W.RaceStatus()
    W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile="current",
        year=YEAR,
        observed_at=OBS - datetime.timedelta(days=5),
        job_id=None,
        person_map={},
        race_status=st,
    )
    session.commit()
    return st


def _srb_shifted(results):
    import dataclasses

    rids = {race_id_from_key(r.key, file_year=YEAR) for r in results}
    out = []
    for s in decode_public("SRB080913", "SRB").rows:
        k = dataclasses.replace(s.key, yy=YEAR % 100)
        if race_id_from_key(k, file_year=YEAR) in rids:
            out.append(dataclasses.replace(s, key=k))
    return out


def test_formal_sed_writes_results_final_odds_and_laps(session):
    results = shifted_results(races=4)
    _field(session, results)
    st = W.RaceStatus()
    c = W.upsert_jrdb_results(
        session,
        results,
        _srb_shifted(results),
        profile="current",
        edition="formal",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        race_status=st,
    )
    session.commit()
    assert c.errors == 0 and c.extra.get("results_inserted_races") == 4
    assert {v["status"] for v in st.applied.values()} == {"applied"}
    n_results = session.scalar(select(func.count()).select_from(RaceResult))
    assert n_results == sum(
        1
        for r in results
        if r.abnormal_code in (None, 0, 5, 6, 3, 4)
        and (r.finish_order or r.abnormal_code in (3, 4))
    )
    # 確定単勝オッズ: every started horse with odds got them (the field had none) — rows only where changed
    with_odds = sum(
        1 for r in results if r.final_win_odds is not None and r.abnormal_code not in (1, 2)
    )
    assert c.extra.get("final_odds_updated") == with_odds
    rid = race_id_from_key(results[0].key, file_year=YEAR)
    hid = horse_id_from_blood_no(results[0].blood_reg_no, race_year=YEAR)
    rh = session.get(RaceHorse, (rid, hid))
    assert rh.odds == results[0].final_win_odds and rh.popularity == results[0].final_win_popularity
    race_prov = session.execute(
        text("SELECT field_group, source FROM race_field_provenance WHERE race_id = :r"), {"r": rid}
    ).all()
    assert ("results", "jrdb") in race_prov and ("laps", "jrdb") in race_prov
    laps = session.get(RaceLaps, rid)
    assert laps.source == "jrdb" and len(laps.lap_times) == 6 and laps.pace_first_3f is not None
    # running a second time: race_atomic refuses (existing), odds unchanged → no value moves
    before = {
        (r.race_id, r.horse_id): (r.odds, r.popularity) for r in session.scalars(select(RaceHorse))
    }
    c2 = W.upsert_jrdb_results(
        session,
        results,
        _srb_shifted(results),
        profile="current",
        edition="formal",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        race_status=W.RaceStatus(),
    )
    session.commit()
    session.expire_all()
    assert c2.extra.get("results_existing_races") == 4 and not c2.extra.get("final_odds_updated")
    assert before == {
        (r.race_id, r.horse_id): (r.odds, r.popularity) for r in session.scalars(select(RaceHorse))
    }


def test_sokuho_sed_writes_nothing(session):
    results = shifted_results(races=2)
    _field(session, results)
    st = W.RaceStatus()
    c = W.upsert_jrdb_results(
        session,
        results,
        [],
        profile="current",
        edition="sokuho",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        race_status=st,
    )
    session.commit()
    assert c.extra.get("sokuho_not_input") == 1 and c.written == 0 and st.applied == {}
    assert session.scalar(select(func.count()).select_from(RaceResult)) == 0
    assert (
        session.scalar(
            select(func.count()).select_from(RaceHorse).where(RaceHorse.odds.is_not(None))
        )
        == 0
    )


def test_hjc_payouts_are_consistent_with_the_final_odds(session):
    results = shifted_results(races=3)
    _field(session, results)
    W.upsert_jrdb_results(
        session,
        results,
        [],
        profile="current",
        edition="formal",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        race_status=W.RaceStatus(),
    )
    st = W.RaceStatus()
    pays = payouts_from_results(results)
    c = W.upsert_jrdb_payouts(
        session, pays, profile="current", year=YEAR, observed_at=OBS, job_id=None, race_status=st
    )
    session.commit()
    assert c.errors == 0 and len(st.applied) == len(pays)
    for p in pays:
        rid = race_id_from_key(p.key, file_year=YEAR)
        win = session.execute(
            select(OfficialWinPayout).where(OfficialWinPayout.race_id == rid)
        ).scalar_one()
        assert win.source == "jrdb_result" and win.payout_yen == p.win[0][1]
        winner_odds = session.execute(
            select(RaceHorse.odds).where(
                RaceHorse.race_id == rid, RaceHorse.horse_number == p.win[0][0]
            )
        ).scalar_one()
        assert Decimal(win.payout_yen) == winner_odds * 100  # HJC 単勝 = 確定オッズ×100
        exotic = (
            session.execute(select(ExoticOdds).where(ExoticOdds.race_id == rid)).scalars().all()
        )
        assert {e.bet_type for e in exotic} == {
            "place",
            "quinella",
            "wide",
            "exacta",
            "trio",
            "trifecta",
        }
        assert all(e.source == "jrdb" for e in exotic)
        prov = dict(
            session.execute(
                text("SELECT field_group, source FROM race_field_provenance WHERE race_id = :r"),
                {"r": rid},
            ).all()
        )
        assert prov["win_payouts"] == "jrdb" and prov["exotic_payouts"] == "jrdb"
    # same file again: no change
    before = session.execute(
        text(
            "SELECT race_id, horse_number, payout_yen, xmin::text FROM official_win_payouts ORDER BY 1, 2"
        )
    ).all()
    W.upsert_jrdb_payouts(
        session,
        pays,
        profile="current",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        race_status=W.RaceStatus(),
    )
    session.commit()
    assert (
        before
        == session.execute(
            text(
                "SELECT race_id, horse_number, payout_yen, xmin::text FROM official_win_payouts ORDER BY 1, 2"
            )
        ).all()
    )


def test_official_owned_payouts_keep_their_source_over_a_same_value_jrdb_file(session):
    results = shifted_results(races=1)
    _field(session, results)
    pays = payouts_from_results(results)
    rid = race_id_from_key(pays[0].key, file_year=YEAR)
    from horseracing_scrape.models import ScrapedWinPayout
    from horseracing_scrape.upsert import (
        OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
        upsert_official_win_payouts,
    )

    upsert_official_win_payouts(
        session,
        rid,
        [ScrapedWinPayout(*pays[0].win[0])],
        observed_at=OBS,
        source=OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
    )
    session.commit()
    W.upsert_jrdb_payouts(
        session,
        pays,
        profile="current",
        year=YEAR,
        observed_at=OBS + datetime.timedelta(hours=1),
        job_id=None,
        race_status=W.RaceStatus(),
    )
    session.commit()
    win = session.execute(
        select(OfficialWinPayout).where(OfficialWinPayout.race_id == rid)
    ).scalar_one()
    assert win.source == "jra_official_result"
    prov = dict(
        session.execute(
            text("SELECT field_group, source FROM race_field_provenance WHERE race_id = :r"),
            {"r": rid},
        ).all()
    )
    assert prov["win_payouts"] == "jra_official"


def test_tyb_body_weight_pair_and_one_way_cancel(session):
    results = shifted_results(races=1)
    _field(session, results)
    pre = prerace_from_results(results)
    rid = race_id_from_key(results[0].key, file_year=YEAR)
    st = W.RaceStatus()
    c = W.upsert_jrdb_prerace(
        session, pre, profile="current", year=YEAR, observed_at=OBS, job_id=None, race_status=st
    )
    session.commit()
    assert c.errors == 0 and st.applied[rid]["status"] == "applied"
    weighed = [r for r in results if r.weight is not None]
    assert c.extra.get("body_weight_written") == len(weighed)
    hid = horse_id_from_blood_no(weighed[0].blood_reg_no, race_year=YEAR)
    rh = session.get(RaceHorse, (rid, hid))
    assert (rh.weight, rh.weight_diff) == (weighed[0].weight, weighed[0].weight_diff)
    # a later TYB that cancels a horse writes started→cancelled; the reverse never happens
    import dataclasses

    cancel = [dataclasses.replace(pre[0], cancelled=True)]
    W.upsert_jrdb_prerace(
        session,
        cancel,
        profile="current",
        year=YEAR,
        observed_at=OBS + datetime.timedelta(minutes=5),
        job_id=None,
        race_status=W.RaceStatus(),
    )
    session.commit()
    session.expire_all()
    assert (
        session.get(
            RaceHorse,
            (
                rid,
                horse_id_from_blood_no(
                    pre[0].blood_reg_no
                    if hasattr(pre[0], "blood_reg_no")
                    else results[0].blood_reg_no,
                    race_year=YEAR,
                ),
            ),
        ).entry_status
        == "cancelled"
    )
    back = [dataclasses.replace(pre[0], cancelled=False)]
    c3 = W.upsert_jrdb_prerace(
        session,
        back,
        profile="current",
        year=YEAR,
        observed_at=OBS + datetime.timedelta(minutes=10),
        job_id=None,
        race_status=W.RaceStatus(),
    )
    session.commit()
    session.expire_all()
    assert (
        session.get(
            RaceHorse, (rid, horse_id_from_blood_no(results[0].blood_reg_no, race_year=YEAR))
        ).entry_status
        == "cancelled"
    )
    assert not c3.extra.get("status_transitions")


def test_formal_sed_fills_going_and_weather_without_replacing_stored_values(session):
    """BAC has no 馬場状態/天候; SED carries them. They fill NULL columns only (the canonical DB
    starts empty; a netkeiba/official value on the current DB is never replaced)."""

    from horseracing_db.models import Race

    from horseracing_scrape.jrdb import normalize as nz

    results = shifted_results(races=4)
    _field(session, results)
    rids = sorted({race_id_from_key(r.key, file_year=YEAR) for r in results})
    assert all(session.get(Race, rid).going is None for rid in rids)  # entries never set them
    kept = rids[0]
    session.execute(
        text("UPDATE races SET going = '不', weather = '雪' WHERE race_id = :r"), {"r": kept}
    )
    session.commit()
    c = W.upsert_jrdb_results(
        session,
        results,
        [],
        profile="current",
        edition="formal",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        race_status=W.RaceStatus(),
    )
    session.commit()
    session.expire_all()
    assert c.errors == 0
    for rid in rids:
        rows = [r for r in results if race_id_from_key(r.key, file_year=YEAR) == rid]
        race = session.get(Race, rid)
        if rid == kept:
            assert (race.going, race.weather) == ("不", "雪")
            continue
        assert race.going == nz.going_from_code(rows[0].going_code) is not None
        assert race.going in {"良", "稍", "重", "不"}  # the spelling races.going already uses
        assert race.weather == nz.weather_from_code(rows[0].weather_code) is not None
    assert c.extra.get("going_filled") == len(rids) - 1
    assert c.extra.get("weather_filled") == len(rids) - 1
