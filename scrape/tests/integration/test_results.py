"""US2 (SC-002): real-results backfill — finish_order + finish_time persisted, INSERT-ONLY."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceHorse, RaceResult
from sqlalchemy import func, select

from horseracing_scrape.pipeline import scrape_entries, scrape_results
from tests._synth import H_WINNER, REAL_RID, real_entries_fetcher, real_results_fetcher

pytestmark = pytest.mark.integration


def test_backfill_finish_order_and_time(session):
    ef, eurls = real_entries_fetcher()
    scrape_entries(session, urls=eurls, fetcher=ef, complete_profiles_after=False)  # 18 started
    rf, rurls = real_results_fetcher()
    summary = scrape_results(session, urls=rurls, fetcher=rf)
    assert summary.status == "succeeded"

    n = session.scalar(select(func.count()).select_from(RaceResult).where(
        RaceResult.race_id == REAL_RID))
    assert n == 18
    win = session.execute(select(
        RaceResult.finish_order, RaceResult.finish_time, RaceResult.finish_time_diff,
        RaceResult.last_3f, RaceResult.corner_orders,
    ).where(RaceResult.race_id == REAL_RID, RaceResult.horse_id == H_WINNER)).one()
    assert win.finish_order == 1
    assert win.finish_time == datetime.timedelta(minutes=2, milliseconds=500)  # "2:00.5"
    assert win.finish_time_diff == datetime.timedelta(0)   # winner is 0s behind itself
    assert win.last_3f is not None                          # 後3F captured
    assert win.corner_orders == ["7", "7", "4", "3"]        # 通過順 captured
    # B: a 脚質 was derived (winner ran from mid-pack -> a JRA-vocab style) into the NULL column
    style = session.scalar(select(RaceHorse.running_style).where(
        RaceHorse.race_id == REAL_RID, RaceHorse.horse_id == H_WINNER))
    assert style in {"逃げ", "先行", "中団", "差し", "追込"}


def test_backfill_does_not_overwrite_jravan(session):
    ef, eurls = real_entries_fetcher()
    scrape_entries(session, urls=eurls, fetcher=ef, complete_profiles_after=False)
    # seed a JRA-VAN result with a DIFFERENT finish_order for the winner
    session.add(RaceResult(race_id=REAL_RID, horse_id=H_WINNER, finish_order=5,
                           result_status=ResultStatus.FINISHED))
    session.commit()

    rf, rurls = real_results_fetcher()  # netkeiba says winner finished 1st
    scrape_results(session, urls=rurls, fetcher=rf)

    fo = session.scalar(select(RaceResult.finish_order).where(
        RaceResult.race_id == REAL_RID, RaceResult.horse_id == H_WINNER))
    assert fo == 5  # existing JRA-VAN row untouched (insert-only)
