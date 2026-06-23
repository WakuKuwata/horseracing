"""US1 (SC-001/002): entries upsert — mapped=canonical, unmapped=nk: + UNMAPPED queue, idempotent."""

from __future__ import annotations

import pytest
from horseracing_db.enums import EntityType, MappingStatus, Source
from horseracing_db.models import IdMapping, Race, RaceHorse
from sqlalchemy import func, select

from horseracing_scrape.pipeline import scrape_entries
from tests._synth import RACE_ID, fixture_fetcher, map_horse

pytestmark = pytest.mark.integration


def test_entries_mapped_and_unmapped(session):
    map_horse(session, netkeiba_id="H001", canonical_id="2020000001")  # H001 is mapped
    fetcher, urls = fixture_fetcher("entries")

    summary = scrape_entries(session, urls=urls, fetcher=fetcher)
    assert summary.status == "succeeded"

    assert session.get(Race, RACE_ID) is not None
    horse_ids = set(session.scalars(
        select(RaceHorse.horse_id).where(RaceHorse.race_id == RACE_ID)
    ))
    assert horse_ids == {"2020000001", "nk:H002", "nk:H003"}  # canonical + surrogates

    # unmapped H002/H003 queued; mapped H001 stays mapped
    unmapped = set(session.scalars(
        select(IdMapping.source_id).where(
            IdMapping.entity_type == EntityType.HORSE, IdMapping.source == Source.NETKEIBA,
            IdMapping.mapping_status == MappingStatus.UNMAPPED,
        )
    ))
    assert unmapped == {"H002", "H003"}

    # cancelled reflected in entry_status
    statuses = dict(session.execute(
        select(RaceHorse.horse_id, RaceHorse.entry_status).where(RaceHorse.race_id == RACE_ID)
    ).all())
    assert statuses["nk:H003"] == "cancelled"


def test_entries_idempotent(session):
    fetcher, urls = fixture_fetcher("entries")
    scrape_entries(session, urls=urls, fetcher=fetcher)
    n1 = session.scalar(select(func.count()).select_from(RaceHorse))
    fetcher2, urls2 = fixture_fetcher("entries")
    scrape_entries(session, urls=urls2, fetcher=fetcher2)
    n2 = session.scalar(select(func.count()).select_from(RaceHorse))
    assert n1 == n2  # re-run: no duplicates


def test_unknown_venue_writes_no_row(session):
    html = (
        '<div class="race" data-year="2025" data-track="99" data-kai="1" data-day="1" '
        'data-raceno="1"><table class="entries"><tr class="horse" data-horse-id="H001" '
        'data-number="1" data-status="started"></tr></table></div>'
    )
    from horseracing_scrape.fetch import FixtureFetcher
    summary = scrape_entries(session, urls=["u"], fetcher=FixtureFetcher({"u": html}))
    assert summary.skipped == 1
    assert session.scalar(select(func.count()).select_from(Race)) == 0  # no fake race_id row
