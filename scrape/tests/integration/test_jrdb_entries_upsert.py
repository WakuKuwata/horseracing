"""178 T041: the JRDB entries writer — canonical rows and masters, same-sha idempotency (values by
PK, writes by xmin), per-race partial failure, the four JRDB-defined columns never reaching the
current DB, and the nk:X alias rule."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import Horse, Jockey, Race, RaceHorse
from sqlalchemy import func, select, text

from horseracing_scrape.jrdb import upsert_jrdb as W
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from tests._jrdb_synth import (
    DAY,
    JST,
    YEAR,
    entries_from_results,
    horses_from_results,
    shifted_programs,
    shifted_results,
    table_snapshot,
)

pytestmark = pytest.mark.integration
OBS = datetime.datetime(2026, 9, 12, 20, 0, tzinfo=JST)


def _ingest(session, results, *, person_map=None, only_races=None, status=None, profile="current"):
    status = status if status is not None else W.RaceStatus()
    c = W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile=profile,
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        person_map=person_map or {},
        race_status=status,
        only_races=only_races,
    )
    session.commit()
    return c, status


def test_a_canonical_rows_and_masters_are_created(session):
    results = shifted_results(races=3)
    pm = {
        ("jockey", results[0].jockey_code): "01019",
        ("trainer", results[0].trainer_code): "01069",
    }
    c, status = _ingest(session, results, person_map=pm)
    rids = sorted({race_id_from_key(r.key, file_year=YEAR) for r in results})
    assert c.errors == 0 and c.extra.get("races_written") == 3
    assert {v["status"] for v in status.applied.values()} == {"applied"} and len(
        status.applied
    ) == 3
    assert status.file_status() == "applied"
    n = session.scalar(select(func.count()).select_from(RaceHorse))
    assert n == len(results)
    assert (
        session.scalar(
            select(func.count()).select_from(RaceHorse).where(RaceHorse.horse_id.like("nk:%"))
        )
        == 0
    )
    race = session.get(Race, rids[0])
    assert race.race_date == DAY and race.track_type in ("芝", "ダ", "障") and race.race_class
    assert race.post_time is not None and race.prize_money == 500
    h = session.get(Horse, horse_id_from_blood_no(results[0].blood_reg_no, race_year=YEAR))
    assert h.data_source == "jrdb" and h.birth_year == int(h.horse_id[:4]) and h.sire_name
    assert h.owner_name == "馬主　太郎氏" and h.sire_line is None  # canonical-only column untouched
    # persons: the mapped code becomes the canonical id (master created), unmapped stays NULL
    rh = session.get(RaceHorse, (rids[0], h.horse_id))
    assert rh.jockey_id == "01019" and session.get(Jockey, "01019") is not None
    unresolved = session.scalar(
        select(func.count()).select_from(RaceHorse).where(RaceHorse.jockey_id.is_(None))
    )
    assert unresolved == c.extra["unresolved_person"] // 2 or unresolved > 0
    prov = (
        session.execute(text("SELECT DISTINCT source FROM race_horse_field_provenance"))
        .scalars()
        .all()
    )
    assert prov == ["jrdb"]


def test_b_same_sha_again_changes_no_product_value_and_writes_nothing(session):
    results = shifted_results(races=3)
    c1, status = _ingest(session, results)
    before = {
        t: table_snapshot(session, t, pk)
        for t, pk in (
            ("races", ("race_id",)),
            ("race_horses", ("race_id", "horse_id")),
            ("horses", ("horse_id",)),
        )
    }
    rids = sorted(status.applied)
    todo = W.unapplied_races(status.applied, rids)
    assert todo == []  # every race applied → nothing to re-process on the same sha
    c2, _ = _ingest(session, results, only_races=todo, status=status)
    assert c2.processed == 0 and c2.written == 0
    session.expire_all()
    after = {
        t: table_snapshot(session, t, pk)
        for t, pk in (
            ("races", ("race_id",)),
            ("race_horses", ("race_id", "horse_id")),
            ("horses", ("horse_id",)),
        )
    }
    for t in before:
        assert before[t][0] == after[t][0], f"{t}: product values changed"
        assert before[t][1] == after[t][1], f"{t}: unneeded writes (xmin moved)"
    # a forced full re-run still changes no product value (the writer is idempotent on values)
    _ingest(session, results)
    session.expire_all()
    again = {
        t: table_snapshot(session, t, pk)
        for t, pk in (
            ("races", ("race_id",)),
            ("race_horses", ("race_id", "horse_id")),
            ("horses", ("horse_id",)),
        )
    }
    for t in before:
        assert before[t][0] == again[t][0], f"{t}: product values changed on forced re-run"


def test_c_one_colliding_race_fails_alone_and_is_reprocessed_alone(session):
    results = shifted_results(races=3)
    rids = sorted({race_id_from_key(r.key, file_year=YEAR) for r in results})
    bad = rids[1]
    # a pre-existing row in race 2 with the SAME 馬番 1 but ANOTHER horse → collision
    session.execute(
        text("INSERT INTO races (race_id, race_date) VALUES (:r, :d)"), {"r": bad, "d": DAY}
    )
    session.execute(text("INSERT INTO horses (horse_id, horse_name) VALUES ('2022999999', '衝突')"))
    session.execute(
        text(
            "INSERT INTO race_horses (race_id, horse_id, horse_number, entry_status) VALUES (:r, '2022999999', 1, 'started')"
        ),
        {"r": bad},
    )
    session.commit()
    c, status = _ingest(session, results)
    assert status.applied[bad]["status"] == "failed"
    assert [status.applied[r]["status"] for r in rids if r != bad] == ["applied", "applied"]
    assert status.file_status() == "partially_applied"
    assert c.extra.get("quarantined_collision") == 1
    # the colliding race wrote nothing (only the pre-seeded row remains)
    assert (
        session.scalar(select(func.count()).select_from(RaceHorse).where(RaceHorse.race_id == bad))
        == 1
    )
    # re-ingest: only the failed race is in scope; the applied ones are untouched (xmin)
    before_vals, before_x = table_snapshot(
        session, "race_horses", ("race_id", "horse_id"), "WHERE race_id <> :b", {"b": bad}
    )
    todo = W.unapplied_races(status.applied, rids)
    assert todo == [bad]
    session.execute(text("DELETE FROM race_horses WHERE race_id = :r"), {"r": bad})
    session.commit()
    c2, status2 = _ingest(session, results, only_races=todo, status=status)
    assert status2.applied[bad]["status"] == "applied" and status2.file_status() == "applied"
    session.expire_all()
    after_vals, after_x = table_snapshot(
        session, "race_horses", ("race_id", "horse_id"), "WHERE race_id <> :b", {"b": bad}
    )
    assert before_vals == after_vals and before_x == after_x


def test_d_profile_current_never_writes_the_four_jrdb_defined_columns(session):
    results = shifted_results(races=2)
    _ingest(session, results)
    status = W.RaceStatus()
    c = W.upsert_jrdb_results(
        session,
        results,
        [],
        profile="current",
        edition="formal",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        race_status=status,
    )
    session.commit()
    assert c.errors == 0 and c.extra.get("results_inserted_races") == 2
    rows = session.execute(
        text(
            "SELECT rr.first_3f, rr.corner_orders, rh.running_style, h.sire_line, h.damsire_line, r.distance "
            "FROM race_results rr JOIN race_horses rh USING (race_id, horse_id) JOIN horses h USING (horse_id) "
            "JOIN races r USING (race_id)"
        )
    ).all()
    assert rows
    for first_3f, corners, style, sire_line, damsire_line, distance in rows:
        assert corners is None and style is None and sire_line is None and damsire_line is None
        if distance != 1200:
            assert (
                first_3f is None
            )  # JRDB's estimate never lands; 1200 m may carry the DB's own identity value
    # the JRDB values themselves differ from NULL, so the guard is what kept them out
    assert any(r.first_3f is not None for r in results if r.distance != 1200)


def test_d2_profile_canonical_writes_them(session):
    results = shifted_results(races=1)
    _ingest(session, results, profile="jrdb_canonical")
    status = W.RaceStatus()
    c = W.upsert_jrdb_results(
        session,
        results,
        [],
        profile="jrdb_canonical",
        edition="formal",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        race_status=status,
    )
    session.commit()
    assert c.errors == 0
    row = session.execute(
        text(
            "SELECT rr.first_3f, rr.corner_orders, rh.running_style, h.sire_line FROM race_results rr "
            "JOIN race_horses rh USING (race_id, horse_id) JOIN horses h USING (horse_id) "
            "WHERE rr.finish_order = 1"
        )
    ).one()
    winner = next(r for r in results if r.finish_order == 1)
    assert row[0] == winner.first_3f and row[2] == winner.running_style_code and row[3] == "1104"
    assert row[1] == [str(x) for x in winner.corner_orders if x is not None]


def test_e_existing_nk_rows_are_written_through_the_alias_rule(session):
    results = shifted_results(races=1)
    rid = race_id_from_key(results[0].key, file_year=YEAR)
    # netkeiba came first with nk: surrogates for the same horses (same 馬番, same names)
    session.execute(
        text(
            "INSERT INTO races (race_id, race_date, race_number, venue_code) VALUES (:r, :d, 1, '01')"
        ),
        {"r": rid, "d": DAY},
    )
    for r in results:
        hid = horse_id_from_blood_no(r.blood_reg_no, race_year=YEAR)
        session.execute(
            text(
                "INSERT INTO horses (horse_id, horse_name, data_source) VALUES (:h, :n, 'netkeiba')"
            ),
            {"h": f"nk:{hid}", "n": r.horse_name},
        )
        session.execute(
            text(
                "INSERT INTO race_horses (race_id, horse_id, horse_number, entry_status) VALUES (:r, :h, :num, 'started')"
            ),
            {"r": rid, "h": f"nk:{hid}", "num": r.horse_number},
        )
    session.commit()
    c, status = _ingest(session, results)
    assert status.applied[rid]["status"] == "applied" and c.extra.get("alias_horses") == len(
        results
    )
    ids = (
        session.execute(select(RaceHorse.horse_id).where(RaceHorse.race_id == rid)).scalars().all()
    )
    assert len(ids) == len(results) and all(
        i.startswith("nk:") for i in ids
    )  # no second row per horse
    assert (
        session.scalar(select(func.count()).select_from(Horse).where(~Horse.horse_id.like("nk:%")))
        == 0
    )
    rh = session.execute(select(RaceHorse).where(RaceHorse.race_id == rid).limit(1)).scalar_one()
    assert rh.frame is not None and rh.entry_status in EntryStatus.ALL
