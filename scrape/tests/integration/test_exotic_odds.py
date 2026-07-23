"""012/080: real exotic dividend ingest — six types, idempotent overwrite, audit, 2007 cutoff.

Feature 080 rewired the parser to the REAL netkeiba result-page markup (Payout_Detail_Table), which
lists WINNING selections only (not the full odds grid). So coverage_scope is inherently 'partial' for
result-page dividends (observed winners << full-grid expected count); see research.md D3.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ExoticOdds, Horse, IngestionJob, Race, RaceHorse
from sqlalchemy import func, select

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.pipeline import scrape_exotic_odds
from tests.conftest import real_fixture

pytestmark = pytest.mark.integration

REAL_RID = "202602011206"
REAL_FIXTURE = "results_202602011206.html"
N_STARTED = 16


def _seed_field(session, race_id=REAL_RID, n=N_STARTED):
    session.merge(Race(race_id=race_id, race_number=int(race_id[-2:]),
                       race_date=datetime.date(2026, 7, 19), venue_code=race_id[4:6]))
    for i in range(1, n + 1):
        session.merge(Horse(horse_id=f"H{race_id}_{i}", horse_name=f"H{i}"))
        session.add(RaceHorse(race_id=race_id, horse_id=f"H{race_id}_{i}", horse_number=i,
                              entry_status=EntryStatus.STARTED))
    session.commit()


def _odds_of(session, bet_type, selection):
    return session.scalar(
        select(ExoticOdds.odds).where(
            ExoticOdds.race_id == REAL_RID, ExoticOdds.bet_type == bet_type,
            ExoticOdds.selection == list(selection),
        )
    )


def _fetcher(html):
    return FixtureFetcher({"u": html}), ["u"]


def test_ingest_stores_six_types_with_coverage(session):
    _seed_field(session)
    fetcher, urls = _fetcher(real_fixture(REAL_FIXTURE))
    summary = scrape_exotic_odds(session, urls=urls, fetcher=fetcher)
    assert summary.status == "succeeded"

    rows = session.scalars(select(ExoticOdds).where(ExoticOdds.race_id == REAL_RID)).all()
    types = {r.bet_type for r in rows}
    assert types == {"place", "quinella", "wide", "trio", "exacta", "trifecta"}
    assert all(r.source == "netkeiba" for r in rows)

    # real dividends list winners only -> every bet type is 'partial' vs the full-grid expected count
    assert {r.coverage_scope for r in rows} == {"partial"}

    # canonical selection + yen/100 odds (from the real Payout_Detail_Table)
    assert _odds_of(session, "place", [1]) == Decimal("1.5")
    assert _odds_of(session, "place", [9]) == Decimal("2.4")
    assert _odds_of(session, "quinella", [1, 9]) == Decimal("20.0")     # sorted
    assert _odds_of(session, "exacta", [1, 9]) == Decimal("32.8")       # order-preserving
    assert _odds_of(session, "trio", [1, 9, 10]) == Decimal("18.9")     # sorted
    assert _odds_of(session, "trifecta", [1, 9, 10]) == Decimal("109.4")
    # wide has 3 winning combos
    assert len([r for r in rows if r.bet_type == "wide"]) == 3


def test_ingest_is_idempotent_overwrite(session):
    _seed_field(session)
    html = real_fixture(REAL_FIXTURE)
    scrape_exotic_odds(session, urls=["u"], fetcher=FixtureFetcher({"u": html}))
    n1 = session.scalar(select(func.count()).select_from(ExoticOdds).where(
        ExoticOdds.race_id == REAL_RID))
    # re-ingest the same page: single-latest overwrite, no duplicate rows (constitution V)
    scrape_exotic_odds(session, urls=["u"], fetcher=FixtureFetcher({"u": html}))
    n2 = session.scalar(select(func.count()).select_from(ExoticOdds).where(
        ExoticOdds.race_id == REAL_RID))
    assert n1 == n2 and n1 > 0
    assert _odds_of(session, "trifecta", [1, 9, 10]) == Decimal("109.4")


def test_ingest_audited_as_exotic_odds_job(session):
    _seed_field(session)
    fetcher, urls = _fetcher(real_fixture(REAL_FIXTURE))
    scrape_exotic_odds(session, urls=urls, fetcher=fetcher)
    job = session.scalars(
        select(IngestionJob).where(IngestionJob.job_type == "exotic_odds")
    ).first()
    assert job is not None and job.status == "succeeded"
    assert job.source == "netkeiba"


def test_pre_2007_race_is_skipped_no_rows(session):
    # same real markup but with a <2007 race_id in the canonical link -> build_race_id rejects it
    html = real_fixture(REAL_FIXTURE).replace(REAL_RID, "200602011206")
    summary = scrape_exotic_odds(session, urls=["u"], fetcher=FixtureFetcher({"u": html}))
    assert summary.skipped == 1  # <2007 -> race_id not constructible, no fake IDs
    assert session.scalar(select(func.count()).select_from(ExoticOdds)) == 0
