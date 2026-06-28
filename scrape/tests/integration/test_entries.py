"""US1 (SC-001): real-entries upsert — mapped=canonical, unmapped=nk:+UNMAPPED (horse/jockey/
trainer), idempotent, invalid venue writes no row."""

from __future__ import annotations

import pytest
from horseracing_db.enums import EntityType, MappingStatus, Source
from horseracing_db.models import IdMapping, Race, RaceHorse
from sqlalchemy import func, select

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.pipeline import scrape_entries
from tests._synth import REAL_RID, map_horse, real_entries_fetcher

pytestmark = pytest.mark.integration


def test_entries_mapped_and_unmapped(session):
    map_horse(session, netkeiba_id="2022103995", canonical_id="2020000001")  # 馬番1 mapped
    fetcher, urls = real_entries_fetcher()

    summary = scrape_entries(session, urls=urls, fetcher=fetcher,
                             complete_profiles_after=False)
    assert summary.status == "succeeded"

    assert session.get(Race, REAL_RID) is not None
    horse_ids = set(session.scalars(
        select(RaceHorse.horse_id).where(RaceHorse.race_id == REAL_RID)
    ))
    assert len(horse_ids) == 18
    assert "2020000001" in horse_ids                       # mapped -> canonical
    assert "nk:2022105102" in horse_ids                    # others -> surrogate
    assert "2022103995" not in horse_ids                   # mapped id not used as surrogate

    # jockey & trainer also queued UNMAPPED (codex coverage gap closed)
    for et in (EntityType.JOCKEY, EntityType.TRAINER):
        n = session.scalar(select(func.count()).select_from(IdMapping).where(
            IdMapping.entity_type == et, IdMapping.source == Source.NETKEIBA,
            IdMapping.mapping_status == MappingStatus.UNMAPPED,
        ))
        assert n and n > 0


def test_entries_idempotent(session):
    fetcher, urls = real_entries_fetcher()
    scrape_entries(session, urls=urls, fetcher=fetcher, complete_profiles_after=False)
    n1 = session.scalar(select(func.count()).select_from(RaceHorse))
    fetcher2, urls2 = real_entries_fetcher()
    scrape_entries(session, urls=urls2, fetcher=fetcher2, complete_profiles_after=False)
    n2 = session.scalar(select(func.count()).select_from(RaceHorse))
    assert n1 == n2 == 18  # re-run: no duplicates


def test_unknown_venue_writes_no_row(session):
    # canonical race_id with an unknown venue (99) -> build_race_id None -> skip, no fake row
    html = (
        "<html><head>"
        '<link rel="canonical" '
        'href="https://race.netkeiba.com/race/shutuba.html?race_id=202499010101" />'
        "</head><body>"
        '<table class="Shutuba_Table"><tr class="HorseList">'
        '<td class="Waku1">1</td><td class="Umaban1">1</td>'
        '<td class="HorseInfo"><a href="https://db.netkeiba.com/horse/2022103995">馬</a></td>'
        "</tr></table></body></html>"
    )
    summary = scrape_entries(session, urls=["u"], fetcher=FixtureFetcher({"u": html}),
                             complete_profiles_after=False)
    assert summary.skipped == 1
    assert session.scalar(select(func.count()).select_from(Race)) == 0
