"""US2 (SC-002): real-results backfill — finish_order + finish_time persisted, INSERT-ONLY."""

from __future__ import annotations

import datetime
import re

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceHorse, RaceResult
from sqlalchemy import func, select

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.pipeline import complete_corner_orders, scrape_entries, scrape_results
from horseracing_scrape.urls import result_url
from tests._synth import H_WINNER, REAL_RID, real_entries_fetcher, real_results_fetcher
from tests.conftest import real_fixture

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


_CORNER_CELL = re.compile(r'(<td class="PassageRate">)\s*[\d-]+\s*(</td>)')


def _race_night_page() -> str:
    """The real result page with every 通過順 cell blanked — what netkeiba serves on race night
    (finishing order and times are there, the corner passing orders are not yet)."""
    html = real_fixture("results_202406050911.html")
    blanked, n = _CORNER_CELL.subn(r"\1\n\2", html)
    assert n == 18, n
    return blanked


def _corners_and_style(session):
    return session.execute(select(RaceResult.finish_order, RaceResult.corner_orders,
                                  RaceHorse.running_style)
                           .join(RaceHorse, (RaceHorse.race_id == RaceResult.race_id)
                                 & (RaceHorse.horse_id == RaceResult.horse_id))
                           .where(RaceResult.race_id == REAL_RID,
                                  RaceResult.horse_id == H_WINNER)).one()


def test_corner_orders_fill_null_only_on_a_later_pass(session):
    """Race-night ingest leaves corner_orders NULL; a later pass fills ONLY that cell."""
    ef, eurls = real_entries_fetcher()
    scrape_entries(session, urls=eurls, fetcher=ef, complete_profiles_after=False)
    scrape_results(session, urls=["u"], fetcher=FixtureFetcher({"u": _race_night_page()}))
    first = _corners_and_style(session)
    assert first.finish_order == 1 and first.corner_orders is None and first.running_style is None

    # simulate an authoritative value that must survive (INSERT-ONLY for everything else)
    session.execute(RaceResult.__table__.update()
                    .where(RaceResult.race_id == REAL_RID, RaceResult.horse_id == H_WINNER)
                    .values(finish_order=5))
    session.commit()

    rf, rurls = real_results_fetcher()  # the complete page, days later
    scrape_results(session, urls=rurls, fetcher=rf)
    later = _corners_and_style(session)
    assert later.corner_orders == ["7", "7", "4", "3"]   # the NULL cell was filled
    assert later.finish_order == 5                        # nothing else was touched
    assert later.running_style in {"逃げ", "先行", "中団", "差し", "追込"}  # derived now


def test_complete_corner_orders_selects_finished_races_with_null_corners(session):
    ef, eurls = real_entries_fetcher()
    scrape_entries(session, urls=eurls, fetcher=ef, complete_profiles_after=False)
    scrape_results(session, urls=["u"], fetcher=FixtureFetcher({"u": _race_night_page()}))
    fetcher = FixtureFetcher({result_url(REAL_RID): real_fixture("results_202406050911.html")})

    # too recent -> not selected, no request made (the fixture would raise on an unknown URL)
    untouched = complete_corner_orders(session, fetcher=fetcher, older_than_days=10_000)
    assert untouched.processed == 0
    assert _corners_and_style(session).corner_orders is None

    done = complete_corner_orders(session, fetcher=fetcher, older_than_days=1)
    assert done.status == "succeeded"
    assert _corners_and_style(session).corner_orders == ["7", "7", "4", "3"]
    # idempotent: nothing left to complete
    again = complete_corner_orders(session, fetcher=fetcher, older_than_days=1)
    assert again.processed == 0
