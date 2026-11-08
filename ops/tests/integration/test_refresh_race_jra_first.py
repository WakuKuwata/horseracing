"""2026-11-07: a race refresh asks the JRA official site (176) for the win odds FIRST and falls
back to netkeiba only when the official step left the race without today's odds.

The official step itself is covered in scrape (test_jra_race_odds); here it is a stub returning
the JobSummary shapes the runner branches on."""

from __future__ import annotations

import datetime
from dataclasses import replace

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob
from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.pipeline import JobSummary
from horseracing_scrape.urls import entries_url, result_url, win_odds_url
from sqlalchemy import select

from horseracing_ops import runner
from horseracing_ops.enqueue import enqueue_race
from tests import conftest as c
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = c.REAL_RID
DAY = datetime.date(2024, 12, 28)


class _Recording(FixtureFetcher):
    def __init__(self, pages):
        super().__init__(pages)
        self.urls: list[str] = []

    def get(self, url, *, use_cache=True):
        self.urls.append(url)
        return super().get(url, use_cache=use_cache)


@pytest.fixture
def pending_fetcher():
    """Entries + win odds fixtures, but a result page that is NOT published yet — so the race stays
    result-pending after the results sub-step (the state in which the official step runs)."""
    return _Recording({
        entries_url(RID): c._read(f"entries_{RID}.html"),
        win_odds_url(RID): c._read(f"odds_{RID}.json"),
        result_url(RID): "<html><body>まだ</body></html>",
    })


def _summary(status, *, written=0, **extra) -> JobSummary:
    errors = 1 if status == JobStatus.FAILED else 0
    return JobSummary("jra_odds", RID, written, written, 0, errors, status, dict(extra))


def _stub(monkeypatch, summary):
    calls: list[tuple[str, datetime.date]] = []

    def fake(session, race_id, *, fetcher, race_date):
        calls.append((race_id, race_date))
        return summary

    monkeypatch.setattr(runner, "refresh_race_win_odds", fake)
    return calls


_FAKE_JRA_FETCHER = object()  # any non-None value: the step itself is stubbed


def _run(session, fetcher, *, origin="manual_ui", jra_fetcher=_FAKE_JRA_FETCHER):
    seed_race(session, race_id=RID, race_date=DAY)
    job, _ = enqueue_race(session, RID, origin=origin)
    session.commit()
    runner.run_one(session, job, fetcher=fetcher, jra_fetcher=jra_fetcher)
    session.refresh(job)
    return job


def _expected_return_jobs(session):
    return session.scalars(
        select(IngestionJob).where(IngestionJob.job_type == "expected_return")).all()


def test_official_adopted_means_no_netkeiba_odds_request(session, pending_fetcher, monkeypatch):
    calls = _stub(monkeypatch, _summary(JobStatus.SUCCEEDED, written=18, odds_written_races=1))
    job = _run(session, pending_fetcher)
    assert calls == [(RID, DAY)]
    assert win_odds_url(RID) not in pending_fetcher.urls  # the fallback was not taken
    assert job.summary["odds_written_by"] == "jra_official"
    assert "jra_odds" not in job.summary  # nothing to report: the official step IS the odds step
    kinds = [x["job_type"] for x in job.summary["calls"]]
    assert "jra_odds" in kinds and "odds" not in kinds
    assert job.status != JobStatus.FAILED
    # 137: a refresh that wrote win odds for a pending race queues the 期待回収率 recompute
    assert len(_expected_return_jobs(session)) == 1


def test_official_failure_falls_back_to_netkeiba_without_painting_partial(
        session, pending_fetcher, monkeypatch):
    _stub(monkeypatch, _summary(JobStatus.FAILED))
    job = _run(session, pending_fetcher)
    assert win_odds_url(RID) in pending_fetcher.urls
    assert job.summary["odds_written_by"] == "netkeiba"
    assert job.summary["jra_odds"]["status"] == JobStatus.FAILED
    assert job.summary["jra_odds"]["fallback"] == "netkeiba"
    kinds = [x["job_type"] for x in job.summary["calls"]]
    assert kinds.index("jra_odds") < kinds.index("odds")
    # the recovered attempt is reported, not counted: same terminal status as a netkeiba-only run
    baseline = _run_baseline(session, pending_fetcher)
    assert job.status == baseline
    assert len(_expected_return_jobs(session)) >= 1


def _run_baseline(session, fetcher):
    """Terminal status of the same refresh with the official route disabled."""
    job, _ = enqueue_race(session, RID, origin="manual_ui")
    session.commit()
    runner.run_one(session, job, fetcher=fetcher, jra_fetcher=None)
    session.refresh(job)
    return job.status


def test_stale_official_page_does_not_fall_back(session, pending_fetcher, monkeypatch):
    _stub(monkeypatch, _summary(JobStatus.SKIPPED, odds_skipped_stale=1))
    job = _run(session, pending_fetcher)
    assert win_odds_url(RID) not in pending_fetcher.urls
    assert job.summary["odds_written_by"] is None
    assert _expected_return_jobs(session) == []


@pytest.mark.parametrize("extra", [
    {"odds_partial_field": 1}, {"odds_prev_day_final_ignored": 1}, {"alias_quarantine": 1}, {},
])
def test_other_official_outcomes_fall_back(session, pending_fetcher, monkeypatch, extra):
    _stub(monkeypatch, _summary(JobStatus.SKIPPED, **extra))
    job = _run(session, pending_fetcher)
    assert win_odds_url(RID) in pending_fetcher.urls
    assert job.summary["odds_written_by"] == "netkeiba"


def test_both_origins_enabled_by_default(session, pending_fetcher, monkeypatch):
    assert runner.CONFIG.jra_odds_origins == ("manual_ui", "daily_bulk")  # user decision 2026-11-07
    calls = _stub(monkeypatch, _summary(JobStatus.SUCCEEDED, written=18, odds_written_races=1))
    job = _run(session, pending_fetcher, origin="daily_bulk")
    assert calls == [(RID, DAY)] and win_odds_url(RID) not in pending_fetcher.urls
    assert job.summary["odds_written_by"] == "jra_official"


def test_origin_not_enabled_skips_the_official_step(session, pending_fetcher, monkeypatch):
    monkeypatch.setattr(runner, "CONFIG", replace(runner.CONFIG, jra_odds_origins=("manual_ui",)))
    calls = _stub(monkeypatch, _summary(JobStatus.SUCCEEDED, written=18, odds_written_races=1))
    job = _run(session, pending_fetcher, origin="daily_bulk")
    assert calls == [] and win_odds_url(RID) in pending_fetcher.urls
    assert job.summary["odds_written_by"] == "netkeiba"


def test_settled_race_skips_the_official_step(session, fixture_fetcher, monkeypatch):
    """The real result fixture settles the race in this pass; final odds come from the result page
    (139), so the official walk is not spent."""
    calls = _stub(monkeypatch, _summary(JobStatus.SUCCEEDED, written=18, odds_written_races=1))
    job = _run(session, fixture_fetcher)
    assert calls == [] and job.summary["odds_written_by"] in (None, "netkeiba")


def test_no_official_fetcher_means_netkeiba_only(session, pending_fetcher, monkeypatch):
    calls = _stub(monkeypatch, _summary(JobStatus.SUCCEEDED, written=18, odds_written_races=1))
    job = _run(session, pending_fetcher, jra_fetcher=None)
    assert calls == [] and job.summary["odds_written_by"] == "netkeiba"


def test_corner_backfill_is_untouched(session, fixture_fetcher, monkeypatch):
    calls = _stub(monkeypatch, _summary(JobStatus.SUCCEEDED, written=18, odds_written_races=1))
    seed_race(session, race_id=RID, race_date=DAY)
    job, _ = enqueue_race(session, RID, origin="corner_backfill")
    session.commit()
    runner.run_one(session, job, fetcher=fixture_fetcher)
    session.refresh(job)
    assert calls == [] and "odds_written_by" not in job.summary
