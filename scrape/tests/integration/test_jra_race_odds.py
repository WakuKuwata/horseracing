"""2026-11-07: one race's official 単複 odds as ONE job (the ops refresh's JRA-first step).

Composes 176's walker/parser/writer for "this race, now" — the outcome contract the ops runner
branches on is ``odds_written_races`` (adopted) / ``odds_skipped_stale`` (stored is newer) /
anything else (fall back to netkeiba)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import IngestionJob, Race
from sqlalchemy import select

from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.race_odds import JOB_TYPE, race_want, refresh_race_win_odds
from tests._jra_fake import FakeJraFetcher
from tests._netkeiba_first import netkeiba_first
from tests._seed176 import race_prov
from tests.jra_fixtures import JST, fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
RID = "202605041111"  # 東京 11R — the one real 単複 fixture
T0822 = datetime.datetime(2026, 11, 1, 8, 22, tzinfo=JST)


def _seed_entries(session):
    page = parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)
    changes = parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)
    obs = fetched_at("entries_tokyo_20261101.sjis.html")
    netkeiba_first(session, page, changes, observed_at=obs - datetime.timedelta(hours=10),
                   race_ids=[RID])
    session.commit()


def _job(session):
    return session.scalars(
        select(IngestionJob).where(IngestionJob.job_type == JOB_TYPE)
        .order_by(IngestionJob.created_at.desc())
    ).first()


def test_race_want_is_venue_and_race_number():
    assert race_want("202605041111") == ("05", {("05", 11)})
    assert race_want("202603030103") == ("03", {("03", 3)})


def test_official_odds_adopted_as_one_race_scoped_job(session):
    _seed_entries(session)
    f = FakeJraFetcher()
    s = refresh_race_win_odds(session, RID, fetcher=f, race_date=D)
    assert s.status == JobStatus.SUCCEEDED and s.written > 0
    assert s.extra["odds_written_races"] == 1 and "odds_skipped_stale" not in s.extra
    # exactly ONE race page was posted, and only the wanted venue's day page was walked
    posted = [c for _, c in f.calls if c and c.startswith("pw151ou")]
    assert len(posted) == 1
    assert not any(c and c.startswith("pw15ols0008") for _, c in f.calls)  # not 京都's day page
    assert s.extra["requests_sent"] == f.requests_sent and s.extra["rewalks"] == 0
    race = session.get(Race, RID)
    assert race.win_odds_official_at == T0822
    assert race_prov(session, RID)["win_odds"][0] == Source.JRA_OFFICIAL
    job = _job(session)
    assert (job.source, job.scope, job.scope_value, job.status) == (
        Source.JRA_OFFICIAL, "race", RID, JobStatus.SUCCEEDED)


def test_second_identical_page_is_stale_not_a_write(session):
    """The stored quote carries the page time; the same page again is 'not newer' — the ops
    runner must NOT fall back to netkeiba on this outcome (nothing is missing)."""
    _seed_entries(session)
    refresh_race_win_odds(session, RID, fetcher=FakeJraFetcher(), race_date=D)
    # a page time EARLIER than the stored one → stale, written 0
    s = refresh_race_win_odds(
        session, RID, fetcher=FakeJraFetcher(odds_time_override="8時10分現在オッズ"), race_date=D)
    assert s.written == 0 and s.extra.get("odds_skipped_stale", 0) >= 1  # win + place
    assert "odds_written_races" not in s.extra


def test_race_not_listed_is_skipped_with_reason(session):
    s = refresh_race_win_odds(session, "202605041113", fetcher=FakeJraFetcher(), race_date=D)
    assert s.status == JobStatus.SKIPPED and s.written == 0 and s.skipped == 1
    assert "not on the official site" in (_job(session).error_message or "")


def test_prev_day_final_is_not_todays_odds(session):
    _seed_entries(session)
    f = FakeJraFetcher(odds_time_override="前日最終オッズ")
    s = refresh_race_win_odds(session, RID, fetcher=f, race_date=D)
    assert s.written == 0 and s.extra.get("odds_prev_day_final_ignored") == 1
    assert "odds_written_races" not in s.extra and "odds_skipped_stale" not in s.extra


def test_before_netkeiba_entries_nothing_is_written(session):
    """FR-011a: the official route creates no rows; without netkeiba's entries the writer skips."""
    session.merge(Race(race_id=RID, race_number=11, race_date=D, venue_code="05"))
    session.commit()
    s = refresh_race_win_odds(session, RID, fetcher=FakeJraFetcher(), race_date=D)
    assert s.written == 0 and "odds_written_races" not in s.extra
    assert "odds_skipped_stale" not in s.extra  # → the runner falls back to netkeiba


def test_walk_failure_is_a_failed_job_not_an_exception(session):
    _seed_entries(session)
    f = FakeJraFetcher(fail_cnames={"pw15oli00/6D"})  # the O index itself: rewalk once, then final
    s = refresh_race_win_odds(session, RID, fetcher=f, race_date=D)
    assert s.status == JobStatus.FAILED and s.written == 0 and s.extra == {}
    assert "injected" in (_job(session).error_message or "")
    assert session.get(Race, RID).win_odds_official_at is None
