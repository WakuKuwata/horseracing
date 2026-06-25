"""T013 (012): exotic odds ingest — idempotent overwrite, coverage, audit, 2007 cutoff (SC-001/002/003)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ExoticOdds, Horse, IngestionJob, Race, RaceHorse
from sqlalchemy import func, select

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.models import ScrapedExoticOdds, ScrapedExoticRow, ScrapedRaceKey
from horseracing_scrape.pipeline import scrape_exotic_odds
from horseracing_scrape.upsert import upsert_exotic_odds
from tests._synth import RACE_ID, fixture_fetcher

pytestmark = pytest.mark.integration


def _seed_started_field(session, n=3):
    """RACE_ID (2025 Tokyo) with n started horses numbered 1..n."""
    session.merge(Race(race_id=RACE_ID, race_number=11, race_date=datetime.date(2025, 6, 1),
                       venue_code="05"))
    for i in range(1, n + 1):
        session.merge(Horse(horse_id=f"H{i}", horse_name=f"H{i}"))
        session.add(RaceHorse(race_id=RACE_ID, horse_id=f"H{i}", horse_number=i,
                              entry_status=EntryStatus.STARTED))
    session.commit()


def _odds_of(session, bet_type, selection):
    return session.scalar(
        select(ExoticOdds.odds).where(
            ExoticOdds.race_id == RACE_ID, ExoticOdds.bet_type == bet_type,
            ExoticOdds.selection == selection,
        )
    )


def test_ingest_stores_six_types_with_coverage(session):
    _seed_started_field(session, n=3)
    fetcher, urls = fixture_fetcher("exotic_odds")
    summary = scrape_exotic_odds(session, urls=urls, fetcher=fetcher)
    assert summary.status == "succeeded"

    rows = session.scalars(select(ExoticOdds).where(ExoticOdds.race_id == RACE_ID)).all()
    types = {r.bet_type for r in rows}
    assert types == {"place", "quinella", "wide", "trio", "exacta", "trifecta"}
    assert all(r.source == "netkeiba" for r in rows)

    # full grids (observed == expected for n=3) vs partial (exacta/trifecta under-supplied)
    scope = {r.bet_type: r.coverage_scope for r in rows}
    assert scope["place"] == "full" and scope["quinella"] == "full"
    assert scope["wide"] == "full" and scope["trio"] == "full"
    assert scope["exacta"] == "partial" and scope["trifecta"] == "partial"

    # canonical selection: trio sorted, exacta order-preserving
    assert _odds_of(session, "trio", [1, 2, 3]) == Decimal("22.5")
    assert _odds_of(session, "exacta", [1, 2]) == Decimal("11.0")
    assert _odds_of(session, "exacta", [2, 1]) == Decimal("18.4")
    # empty-odds exacta row [3,1] skipped
    assert _odds_of(session, "exacta", [3, 1]) is None


def test_ingest_is_idempotent_overwrite(session):
    _seed_started_field(session, n=3)
    fetcher, urls = fixture_fetcher("exotic_odds")
    scrape_exotic_odds(session, urls=urls, fetcher=fetcher)
    n1 = session.scalar(select(func.count()).select_from(ExoticOdds))
    fetcher2, urls2 = fixture_fetcher("exotic_odds")
    scrape_exotic_odds(session, urls=urls2, fetcher=fetcher2)
    n2 = session.scalar(select(func.count()).select_from(ExoticOdds))
    assert n1 == n2  # re-ingest overwrites, no duplicate rows

    # explicit overwrite to a new (final dividend) value, still single row
    key = ScrapedRaceKey(year=2025, track_code="05", kai=2, nichime=3, race_no=11)
    upsert_exotic_odds(session, RACE_ID, ScrapedExoticOdds(
        key=key, rows=(ScrapedExoticRow("trio", (1, 2, 3), 99.9),)
    ))
    session.commit()
    session.expire_all()
    rows = session.scalars(
        select(ExoticOdds).where(ExoticOdds.race_id == RACE_ID, ExoticOdds.bet_type == "trio")
    ).all()
    assert len(rows) == 1 and float(rows[0].odds) == 99.9


def test_ingest_audited_as_exotic_odds_job(session):
    _seed_started_field(session, n=3)
    fetcher, urls = fixture_fetcher("exotic_odds")
    scrape_exotic_odds(session, urls=urls, fetcher=fetcher)
    job = session.scalars(
        select(IngestionJob).where(IngestionJob.job_type == "exotic_odds")
    ).first()
    assert job is not None and job.status == "succeeded"
    assert job.source == "netkeiba"


def test_pre_2007_race_is_skipped_no_rows(session):
    html = (
        '<html><body><div class="race" data-year="2006" data-track="05" data-kai="2" '
        'data-day="3" data-raceno="11">'
        '<table class="exotic" data-bet-type="trio">'
        '<tr class="combo" data-horses="1-2-3" data-odds="10.0"></tr></table>'
        "</div></body></html>"
    )
    summary = scrape_exotic_odds(session, urls=["u"], fetcher=FixtureFetcher({"u": html}))
    assert summary.skipped == 1  # <2007 -> race_id not constructible, no fake IDs
    assert session.scalar(select(func.count()).select_from(ExoticOdds)) == 0
