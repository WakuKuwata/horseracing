"""US2 (SC-002): real-results backfill — finish_order + finish_time persisted, INSERT-ONLY."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceResult
from sqlalchemy import func, select

from horseracing_scrape.pipeline import scrape_entries, scrape_results
from tests._synth import H_WINNER, REAL_RID, real_entries_fetcher, real_results_fetcher

pytestmark = pytest.mark.integration


def test_backfill_finish_order_and_time(session):
    ef, eurls = real_entries_fetcher()
    scrape_entries(session, urls=eurls, fetcher=ef)  # 18 started
    rf, rurls = real_results_fetcher()
    summary = scrape_results(session, urls=rurls, fetcher=rf)
    assert summary.status == "succeeded"

    n = session.scalar(select(func.count()).select_from(RaceResult).where(
        RaceResult.race_id == REAL_RID))
    assert n == 18
    win = session.execute(select(RaceResult.finish_order, RaceResult.finish_time).where(
        RaceResult.race_id == REAL_RID, RaceResult.horse_id == H_WINNER)).one()
    assert win.finish_order == 1
    assert win.finish_time == datetime.timedelta(minutes=2, milliseconds=500)  # "2:00.5"


def test_backfill_does_not_overwrite_jravan(session):
    ef, eurls = real_entries_fetcher()
    scrape_entries(session, urls=eurls, fetcher=ef)
    # seed a JRA-VAN result with a DIFFERENT finish_order for the winner
    session.add(RaceResult(race_id=REAL_RID, horse_id=H_WINNER, finish_order=5,
                           result_status=ResultStatus.FINISHED))
    session.commit()

    rf, rurls = real_results_fetcher()  # netkeiba says winner finished 1st
    scrape_results(session, urls=rurls, fetcher=rf)

    fo = session.scalar(select(RaceResult.finish_order).where(
        RaceResult.race_id == REAL_RID, RaceResult.horse_id == H_WINNER))
    assert fo == 5  # existing JRA-VAN row untouched (insert-only)
