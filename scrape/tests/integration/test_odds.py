"""US3 (SC-003): real win-odds update result-pending races only (odds + popularity); protect
JRA-VAN final odds when results exist."""

from __future__ import annotations

from decimal import Decimal

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceHorse, RaceResult
from sqlalchemy import select

from horseracing_scrape.pipeline import scrape_entries, scrape_odds
from tests._synth import H_NUM1, REAL_RID, real_entries_fetcher, real_odds_fetcher

pytestmark = pytest.mark.integration


def _row(session, horse_id):
    return session.execute(select(RaceHorse.odds, RaceHorse.popularity).where(
        RaceHorse.race_id == REAL_RID, RaceHorse.horse_id == horse_id)).one()


def test_odds_and_popularity_update_when_pending(session):
    ef, eurls = real_entries_fetcher()
    scrape_entries(session, urls=eurls, fetcher=ef)
    of, ourls = real_odds_fetcher()
    summary = scrape_odds(session, urls=ourls, fetcher=of)
    assert summary.status == "succeeded"
    odds, pop = _row(session, H_NUM1)  # 馬番1
    assert odds == Decimal("19.1") and pop == 6


def test_odds_protected_when_results_exist(session):
    ef, eurls = real_entries_fetcher()
    scrape_entries(session, urls=eurls, fetcher=ef)
    session.add(RaceResult(race_id=REAL_RID, horse_id=H_NUM1, finish_order=1,
                           result_status=ResultStatus.FINISHED))
    session.execute(RaceHorse.__table__.update().where(
        RaceHorse.race_id == REAL_RID, RaceHorse.horse_id == H_NUM1).values(odds=Decimal("9.9")))
    session.commit()

    of, ourls = real_odds_fetcher()
    summary = scrape_odds(session, urls=ourls, fetcher=of)
    assert summary.skipped == 1  # race has results -> odds protected
    odds, _ = _row(session, H_NUM1)
    assert odds == Decimal("9.9")  # JRA-VAN final odds untouched
