"""178 (2026-11-10): the SED rider (the one who actually rode) replaces the KYI rider on a
乗り替わり — same timed jockey rule as the entries step; 取消/除外, unmapped codes, an older
observation and a call without ``person_map`` leave the stored rider alone."""

from __future__ import annotations

import dataclasses
import datetime

import pytest
from horseracing_db.models import Jockey, RaceHorse
from sqlalchemy import select, text

from horseracing_scrape.jrdb import upsert_jrdb as W
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from tests._jrdb_synth import (
    JST,
    YEAR,
    entries_from_results,
    horses_from_results,
    shifted_programs,
    shifted_results,
)

pytestmark = pytest.mark.integration
OBS = datetime.datetime(2026, 9, 12, 20, 0, tzinfo=JST)
CARD, ACTUAL = "01019", "05630"


def _setup(session, profile):
    results = shifted_results(races=1)
    card_code = results[0].jockey_code
    pm = {("jockey", r.jockey_code): f"9{i:04d}" for i, r in enumerate(results)}
    pm[("jockey", card_code)] = CARD
    W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile=profile,
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        person_map=pm,
        race_status=W.RaceStatus(),
    )
    session.commit()
    return results, pm


def _results(session, results, pm, *, profile, observed_at, person_map="pm"):
    c = W.upsert_jrdb_results(
        session,
        results,
        [],
        profile=profile,
        edition="formal",
        year=YEAR,
        observed_at=observed_at,
        job_id=None,
        race_status=W.RaceStatus(),
        **({} if person_map is None else {"person_map": pm}),
    )
    session.commit()
    return c


def _rider(session, r):
    rid = race_id_from_key(r.key, file_year=YEAR)
    hid = horse_id_from_blood_no(r.blood_reg_no, race_year=YEAR)
    return session.get(RaceHorse, (rid, hid)).jockey_id


@pytest.mark.parametrize("profile", ["current", "jrdb_canonical"])
def test_a_change_of_rider_is_written_with_master_and_provenance(session, profile):
    results, pm = _setup(session, profile)
    assert _rider(session, results[0]) == CARD
    pm[("jockey", "77777")] = ACTUAL
    sed = [
        dataclasses.replace(results[0], jockey_code="77777", jockey_name="代打　騎手")
    ] + results[1:]
    later = OBS + datetime.timedelta(hours=1)
    c = _results(session, sed, pm, profile=profile, observed_at=later)
    assert c.errors == 0 and c.extra.get("results_jockey_changes") == 1
    session.expire_all()
    assert _rider(session, results[0]) == ACTUAL
    assert session.get(Jockey, ACTUAL).jockey_name == "代打　騎手"
    others = [_rider(session, r) for r in results[1:]]
    assert others == [pm[("jockey", r.jockey_code)] for r in results[1:]]  # unchanged riders
    src = session.execute(
        text(
            "SELECT source FROM race_horse_field_provenance WHERE horse_id = :h AND field_group = 'jockey'"
        ),
        {"h": horse_id_from_blood_no(results[0].blood_reg_no, race_year=YEAR)},
    ).scalar_one()
    assert src == "jrdb"
    # a re-ingest of the same result file writes nothing more
    c2 = _results(session, sed, pm, profile=profile, observed_at=later)
    assert not c2.extra.get("results_jockey_changes")


def test_b_cases_that_keep_the_card_rider(session):
    results, pm = _setup(session, "jrdb_canonical")
    later = OBS + datetime.timedelta(hours=1)
    # unmapped code
    sed = [dataclasses.replace(results[0], jockey_code="88888")] + results[1:]
    c = _results(session, sed, pm, profile="jrdb_canonical", observed_at=later)
    assert c.extra.get("results_jockey_unresolved") == 1 and _rider(session, results[0]) == CARD
    # no person_map: the behaviour before 2026-11-10
    pm[("jockey", "88888")] = ACTUAL
    session.execute(text("DELETE FROM race_results"))
    session.commit()
    c = _results(session, sed, pm, profile="jrdb_canonical", observed_at=later, person_map=None)
    assert not c.extra.get("results_jockey_changes") and _rider(session, results[0]) == CARD
    # an observation older than the stored rider's is refused
    session.execute(text("DELETE FROM race_results"))
    session.commit()
    c = _results(
        session, sed, pm, profile="jrdb_canonical", observed_at=OBS - datetime.timedelta(hours=1)
    )
    assert (
        c.extra.get("results_jockey_older_observation") == len(results)
        and _rider(session, results[0]) == CARD
    )


def test_c_scratched_horse_keeps_its_card_rider(session):
    results, pm = _setup(session, "jrdb_canonical")
    pm[("jockey", "77777")] = ACTUAL
    sed = [
        dataclasses.replace(results[0], jockey_code="77777", abnormal_code=1, finish_order=0)
    ] + results[1:]
    c = _results(
        session, sed, pm, profile="jrdb_canonical", observed_at=OBS + datetime.timedelta(hours=1)
    )
    assert c.errors == 0 and not c.extra.get("results_jockey_changes")
    assert _rider(session, results[0]) == CARD
    assert session.scalar(select(Jockey).where(Jockey.jockey_id == ACTUAL)) is None


def test_d_a_card_reapplied_after_the_results_keeps_the_rider_who_rode(session):
    results, pm = _setup(session, "jrdb_canonical")
    pm[("jockey", "77777")] = ACTUAL
    sed = [dataclasses.replace(results[0], jockey_code="77777")] + results[1:]
    _results(
        session, sed, pm, profile="jrdb_canonical", observed_at=OBS + datetime.timedelta(hours=1)
    )
    assert _rider(session, results[0]) == ACTUAL
    c = W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile="jrdb_canonical",
        year=YEAR,
        observed_at=OBS + datetime.timedelta(hours=5),
        job_id=None,
        person_map=pm,
        race_status=W.RaceStatus(),
    )
    session.commit()
    session.expire_all()
    assert c.extra.get("card_jockey_kept_after_results") == 1
    assert _rider(session, results[0]) == ACTUAL


def test_e_a_same_rider_observation_still_advances_the_clock(session):
    """t1 card A → t3 result A → t2 result B: B is older than the last observation, so A stays."""
    results, pm = _setup(session, "jrdb_canonical")
    _results(
        session,
        results,
        pm,
        profile="jrdb_canonical",
        observed_at=OBS + datetime.timedelta(hours=3),
    )
    pm[("jockey", "77777")] = ACTUAL
    sed = [dataclasses.replace(results[0], jockey_code="77777")] + results[1:]
    c = _results(
        session, sed, pm, profile="jrdb_canonical", observed_at=OBS + datetime.timedelta(hours=2)
    )
    assert c.extra.get("results_jockey_older_observation") == len(results)
    assert _rider(session, results[0]) == CARD
