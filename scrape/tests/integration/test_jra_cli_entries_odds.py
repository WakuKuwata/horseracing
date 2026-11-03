"""176 T058: jra-entries / jra-odds through run_command with a fixture fetcher and a temp DB.

FR-011a: the netkeiba route's 出馬表 is laid down first (``nk:`` rows); the official route writes
onto it and creates no row."""

from __future__ import annotations

import argparse
import datetime

import pytest
from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import (
    IngestionJob,
    Race,
    RaceFieldProvenance,
    RaceHorseFieldProvenance,
)
from sqlalchemy import event, func, select

from horseracing_scrape.jra.cli import run_command
from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from tests._jra_fake import FakeJraFetcher
from tests._netkeiba_first import netkeiba_first
from tests.jra_fixtures import JST, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
_BUSINESS = (
    "races",
    "race_horses",
    "horses",
    "jockeys",
    "trainers",
    "ingestion_jobs",
    "race_field_provenance",
    "race_horse_field_provenance",
)


def _args(**over):
    base = dict(
        date=D.isoformat(),
        venue="05",
        race_ids=None,
        dry_run=False,
        json=True,
        database_url=None,
        policy=None,
        archive_dir=None,
        command="jra-entries",
    )
    base.update(over)
    return argparse.Namespace(**base)


def _seed_netkeiba(session):
    netkeiba_first(
        session,
        parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D),
        parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D),
        observed_at=datetime.datetime(2026, 10, 31, 18, 0, tzinfo=JST),
    )


def _counts(session):
    return {
        t: session.execute(func.count()).scalar()
        if False
        else session.execute(
            __import__("sqlalchemy").text(f"SELECT count(*) FROM {t}")
        ).scalar_one()
        for t in _BUSINESS
    }


def test_dry_run_sends_no_dml_and_writes_nothing(session, session_factory, engine):
    f = FakeJraFetcher()
    before = _counts(session)
    dml: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        s = statement.lstrip().upper()
        if s.startswith(("INSERT", "UPDATE", "DELETE")) and "fetch_throttle_state" not in statement:
            dml.append(statement[:60])

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        r = run_command(
            "jra-entries", _args(dry_run=True), fetcher=f, session_factory=session_factory, date=D
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert r.dry_run and r.jobs == [] and r.exit_code == 0
    assert dml == []
    assert _counts(session) == before
    assert r.requests_sent == f.requests_sent >= 4  # index D + day list + entries + changes
    assert any("races_published" in n for n in r.notes)


def test_entries_run_writes_jobs_and_rows(session, session_factory):
    _seed_netkeiba(session)
    before = _counts(session)
    f = FakeJraFetcher()
    r = run_command("jra-entries", _args(), fetcher=f, session_factory=session_factory, date=D)
    assert r.exit_code == 0, r.to_json()
    assert [j.job_type for j in r.jobs] == ["jra_entries", "jra_changes"]
    assert all(j.status == JobStatus.SUCCEEDED for j in r.jobs)
    j_entries, j_changes = r.jobs
    assert j_entries.extra["requests_sent"] >= 4 and j_entries.extra["races_published"] == 12
    assert (
        j_changes.extra["requests_sent"] == 0
        and j_changes.extra.get("shared_fetch_with_jra_entries") == 1
    )
    assert j_changes.extra.get("post_time_changes") == 1  # 10R 15:00 → 15:01
    jobs = session.scalars(select(IngestionJob)).all()
    assert {j.source for j in jobs} == {Source.JRA_OFFICIAL}
    after = _counts(session)
    for t in ("races", "race_horses", "horses", "jockeys", "trainers"):
        assert after[t] == before[t], t  # no row created by the official route (FR-011a)
    assert j_entries.extra.get("alias_horses") == after["race_horses"]
    race10 = session.get(Race, "202605041110")
    assert (
        race10.post_time.astimezone(datetime.timezone(datetime.timedelta(hours=9))).strftime(
            "%H:%M"
        )
        == "15:01"
    )
    # the official provenance rows reference the job that wrote them (the netkeiba seed has none)
    job_ids = {j.ingestion_job_id for j in jobs}
    official = Source.JRA_OFFICIAL
    prov_jobs = set(
        session.scalars(
            select(RaceHorseFieldProvenance.ingestion_job_id).where(
                RaceHorseFieldProvenance.source == official
            )
        )
    )
    assert prov_jobs <= job_ids and prov_jobs
    race_jobs = set(
        session.scalars(
            select(RaceFieldProvenance.ingestion_job_id).where(
                RaceFieldProvenance.source == official
            )
        )
    )
    assert race_jobs <= job_ids and race_jobs
    doc = r.to_json()
    assert doc["command"] == "jra-entries" and doc["jobs"][0]["status"] == "succeeded"
    assert doc["quarantined"] == [] and doc["exit_code"] == 0


def test_race_id_filter_and_unsupported_date(session, session_factory):
    _seed_netkeiba(session)
    f = FakeJraFetcher()
    r = run_command(
        "jra-entries",
        _args(race_ids=["202605041111"]),
        fetcher=f,
        session_factory=session_factory,
        date=D,
    )
    assert r.exit_code == 0
    official = set(
        session.scalars(
            select(RaceHorseFieldProvenance.race_id).where(
                RaceHorseFieldProvenance.source == Source.JRA_OFFICIAL
            )
        )
    )
    assert official == {"202605041111"}
    f2 = FakeJraFetcher()
    r2 = run_command(
        "jra-entries",
        _args(date="2026-12-25"),
        fetcher=f2,
        session_factory=session_factory,
        date=datetime.date(2026, 12, 25),
    )
    assert r2.unsupported_date == 1 and r2.exit_code == 0
    assert all(j.status == JobStatus.SKIPPED for j in r2.jobs)


def test_quarantine_makes_the_job_partial(session, session_factory):
    # 1R 馬番 1 is stored as nk:2024107186 but its master carries another name → no alias
    from horseracing_db.models import Horse

    _seed_netkeiba(session)
    session.execute(
        Horse.__table__.update()
        .where(Horse.horse_id == "nk:2024107186")
        .values(horse_name="別の馬")
    )
    session.commit()
    r = run_command(
        "jra-entries", _args(), fetcher=FakeJraFetcher(), session_factory=session_factory, date=D
    )
    assert r.exit_code == 1
    assert r.jobs[0].status == JobStatus.PARTIAL
    assert r.jobs[0].extra.get("quarantined_collision") == 1
    assert r.jobs[0].extra.get("alias_name_mismatch") == 1
    assert any("202605041101" in q for q in r.quarantined)
    official = set(
        session.scalars(
            select(RaceHorseFieldProvenance.race_id).where(
                RaceHorseFieldProvenance.source == Source.JRA_OFFICIAL
            )
        )
    )
    assert len(official) == 11 and "202605041101" not in official  # the other 11 were written


def test_entries_before_netkeiba_waits_and_writes_nothing(session, session_factory):
    r = run_command(
        "jra-entries", _args(), fetcher=FakeJraFetcher(), session_factory=session_factory, date=D
    )
    assert r.jobs[0].extra.get("awaiting_netkeiba_entries") == 12
    assert not r.jobs[0].extra.get("quarantined_collision")
    c = _counts(session)
    for t in ("races", "race_horses", "horses", "jockeys", "trainers"):
        assert c[t] == 0, t


def test_odds_after_entries_limited_to_one_race(session, session_factory):
    _seed_netkeiba(session)
    run_command(
        "jra-entries", _args(), fetcher=FakeJraFetcher(), session_factory=session_factory, date=D
    )
    f = FakeJraFetcher()
    r = run_command(
        "jra-odds",
        _args(command="jra-odds", race_ids=["202605041111"]),
        fetcher=f,
        session_factory=session_factory,
        date=D,
    )
    assert r.exit_code == 0, r.to_json()
    assert r.jobs[0].job_type == "jra_odds" and r.jobs[0].status == JobStatus.SUCCEEDED
    assert r.jobs[0].extra.get("odds_written_races") == 1
    # index O + day page + exactly one race page (+ no robots in the fake)
    posted = [c for u, c in f.calls if c and c.startswith("pw151ou")]
    assert len(posted) == 1
    race = session.get(Race, "202605041111")
    assert race.win_odds_official_at is not None
