"""176 T073: jra-results (two jobs, one fetch) and jra-payouts through run_command."""

from __future__ import annotations

import argparse
import datetime

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import (
    ExoticOdds,
    IngestionJob,
    OfficialWinPayout,
    RaceFieldProvenance,
    RaceResult,
)
from sqlalchemy import event, func, select, text

from horseracing_scrape.jra.cli import run_command
from tests._jra_fake import FakeJraFetcher
from tests._seed_results176 import D, seed_from_results

pytestmark = pytest.mark.integration


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
        command="jra-results",
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_results_writes_two_jobs_from_one_fetch(session, session_factory):
    seed_from_results(session)
    f = FakeJraFetcher()
    r = run_command("jra-results", _args(), fetcher=f, session_factory=session_factory, date=D)
    assert r.exit_code == 0, r.to_json()
    assert [j.job_type for j in r.jobs] == ["jra_results", "jra_payouts"]
    j_res, j_pay = r.jobs
    assert j_res.extra["requests_sent"] == 3 == f.requests_sent  # index S + day list + results page
    assert j_pay.extra["requests_sent"] == 0 and j_pay.extra["shared_fetch_with_jra_results"] == 1
    assert j_res.extra["results_inserted_races"] == 12
    assert j_pay.extra["payout_complete"] == 84 and j_pay.extra["not_mapped_bet_types"] == 12
    assert session.scalar(select(func.count()).select_from(OfficialWinPayout)) == 12
    # provenance FKs point at the job that wrote each group
    ids = {j.job_type: j.ingestion_job_id for j in session.scalars(select(IngestionJob))}
    prov = {
        (p.race_id, p.field_group): p.ingestion_job_id
        for p in session.scalars(select(RaceFieldProvenance))
    }
    assert prov[("202605041001", "results")] == ids["jra_results"]
    assert prov[("202605041001", "exotic_payouts")] == ids["jra_payouts"]
    assert prov[("202605041001", "win_payouts")] == ids["jra_payouts"]


def test_payouts_alone_and_race_id_filter(session, session_factory):
    seed_from_results(session)
    f = FakeJraFetcher()
    r = run_command(
        "jra-payouts",
        _args(command="jra-payouts", race_ids=["202605041011"]),
        fetcher=f,
        session_factory=session_factory,
        date=D,
    )
    assert r.exit_code == 0, r.to_json()
    assert [j.job_type for j in r.jobs] == ["jra_payouts"] and r.jobs[0].extra["requests_sent"] == 2
    assert {x.race_id for x in session.scalars(select(OfficialWinPayout))} == {"202605041011"}
    assert session.scalar(select(func.count()).select_from(RaceResult)) == 0


def test_dry_run_sends_no_dml(session, session_factory, engine):
    seed_from_results(session)
    before = session.execute(text("SELECT count(*) FROM race_results")).scalar_one()
    dml = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            dml.append(statement[:50])

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        r = run_command(
            "jra-results",
            _args(dry_run=True),
            fetcher=FakeJraFetcher(),
            session_factory=session_factory,
            date=D,
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert r.dry_run and r.jobs == [] and dml == [] and r.exit_code == 0
    assert session.execute(text("SELECT count(*) FROM race_results")).scalar_one() == before
    assert session.scalar(select(func.count()).select_from(ExoticOdds)) == 0


def test_results_for_races_missing_entries_is_partial(session, session_factory):
    seed_from_results(session, race_ids=["202605041001"])
    r = run_command(
        "jra-results", _args(), fetcher=FakeJraFetcher(), session_factory=session_factory, date=D
    )
    assert r.jobs[0].extra["results_inserted_races"] == 1
    assert r.jobs[0].extra["results_race_not_in_db"] == 11
    # payouts for the 11 races without a races row are skipped, not failed
    assert r.jobs[1].extra["payout_race_not_in_db"] == 11
    assert r.jobs[1].status in (JobStatus.SUCCEEDED, JobStatus.PARTIAL)


def test_unsupported_date(session, session_factory):
    other = datetime.date(2026, 12, 25)
    r = run_command(
        "jra-results",
        _args(date=other.isoformat()),
        fetcher=FakeJraFetcher(),
        session_factory=session_factory,
        date=other,
    )
    assert r.unsupported_date == 1 and r.exit_code == 0
    assert [j.status for j in r.jobs] == [JobStatus.SKIPPED, JobStatus.SKIPPED]
