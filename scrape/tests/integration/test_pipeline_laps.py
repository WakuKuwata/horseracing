"""Feature 034 (integration): lap ingest pipeline on a migrated PostgreSQL testcontainer, fed the
real saved db.netkeiba fixture via FixtureFetcher (no network)."""

from __future__ import annotations

import pytest
from horseracing_db.models import Race, RaceLaps
from sqlalchemy.orm import Session

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.pipeline import scrape_laps
from horseracing_scrape.urls import race_db_url
from tests.conftest import real_fixture

pytestmark = pytest.mark.integration

_RID = "202406050911"
_FIX = "db_race_202406050911.html"


def _seed_race(session: Session, race_id: str = _RID) -> None:
    session.add(Race(race_id=race_id, distance=2000, race_number=11))
    session.commit()


def test_scrape_laps_writes_row(session: Session):
    _seed_race(session)
    fetcher = FixtureFetcher({race_db_url(_RID): real_fixture(_FIX)})
    summ = scrape_laps(session, race_ids=[_RID], fetcher=fetcher, scope_value="t")
    assert summ.written == 1 and summ.errors == 0
    row = session.get(RaceLaps, _RID)
    assert len(row.lap_times) == 10
    assert float(row.pace_first_3f) == 36.0 and float(row.pace_last_3f) == 35.5


def test_scrape_laps_skips_race_without_row(session: Session):
    # no Race row → skipped, no FK violation, no fake row
    fetcher = FixtureFetcher({race_db_url("202401010199"): real_fixture(_FIX)})
    summ = scrape_laps(session, race_ids=["202401010199"], fetcher=fetcher, scope_value="t")
    assert summ.written == 0 and summ.skipped == 1
    assert session.query(RaceLaps).count() == 0


def test_scrape_laps_idempotent_overwrite(session: Session):
    _seed_race(session)
    fetcher = FixtureFetcher({race_db_url(_RID): real_fixture(_FIX)})
    scrape_laps(session, race_ids=[_RID], fetcher=fetcher, scope_value="t")
    scrape_laps(session, race_ids=[_RID], fetcher=fetcher, scope_value="t")
    assert session.query(RaceLaps).count() == 1   # single-latest, no duplicate


def test_scrape_laps_resilient_to_page_error(session: Session):
    # Feature 038: one page that raises on fetch must NOT abort the whole backfill — it is recorded
    # as an error/skip while the good race still gets ingested (single failure ≠ job failure).
    _seed_race(session, "202406050911")
    _seed_race(session, "202406050912")

    class _FlakyFetcher:
        def get(self, url: str, *, use_cache: bool = True) -> str:
            if "202406050912" in url:
                raise RuntimeError("simulated fetch failure")
            return real_fixture(_FIX)

    summ = scrape_laps(session, race_ids=["202406050912", "202406050911"],
                       fetcher=_FlakyFetcher(), scope_value="t")
    assert str(summ.status) == "partial"  # not FAILED
    assert summ.written == 1 and summ.errors == 1
    assert session.get(RaceLaps, "202406050911") is not None  # the good race still ingested
