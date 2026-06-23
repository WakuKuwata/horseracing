"""US2 (SC-004): pre-race odds update result-pending races only; protect JRA-VAN final odds."""

from __future__ import annotations

from decimal import Decimal

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceHorse, RaceResult
from sqlalchemy import select

from horseracing_scrape.pipeline import scrape_entries, scrape_odds
from tests._synth import RACE_ID, fixture_fetcher

pytestmark = pytest.mark.integration


def _odds(session, horse_id):
    return session.scalar(
        select(RaceHorse.odds).where(RaceHorse.race_id == RACE_ID, RaceHorse.horse_id == horse_id)
    )


def test_odds_update_when_result_pending(session):
    fetcher, urls = fixture_fetcher("entries")
    scrape_entries(session, urls=urls, fetcher=fetcher)
    of, ourls = fixture_fetcher("odds")
    summary = scrape_odds(session, urls=ourls, fetcher=of)
    assert summary.status == "succeeded"
    assert _odds(session, "nk:H001") == Decimal("3.4")
    assert _odds(session, "nk:H002") == Decimal("2.1")


def test_odds_protected_when_results_exist(session):
    fetcher, urls = fixture_fetcher("entries")
    scrape_entries(session, urls=urls, fetcher=fetcher)
    # simulate JRA-VAN final state: a result row + a known final odds
    session.add(RaceResult(race_id=RACE_ID, horse_id="nk:H001", finish_order=1,
                           result_status=ResultStatus.FINISHED))
    session.execute(
        RaceHorse.__table__.update()
        .where(RaceHorse.race_id == RACE_ID, RaceHorse.horse_id == "nk:H001")
        .values(odds=Decimal("9.9"))
    )
    session.commit()

    of, ourls = fixture_fetcher("odds")
    summary = scrape_odds(session, urls=ourls, fetcher=of)
    assert summary.skipped == 1  # race has results -> odds protected
    assert _odds(session, "nk:H001") == Decimal("9.9")  # JRA-VAN final odds untouched
