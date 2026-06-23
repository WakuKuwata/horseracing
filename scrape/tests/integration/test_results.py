"""US3 (SC-005): results backfill is INSERT-ONLY (never overwrites JRA-VAN); no row for non-starters."""

from __future__ import annotations

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceResult
from sqlalchemy import select

from horseracing_scrape.pipeline import scrape_entries, scrape_results
from tests._synth import RACE_ID, fixture_fetcher

pytestmark = pytest.mark.integration


def test_backfill_inserts_for_started_only(session):
    fetcher, urls = fixture_fetcher("entries")
    scrape_entries(session, urls=urls, fetcher=fetcher)  # H001/H002 started, H003 cancelled
    rf, rurls = fixture_fetcher("results")
    summary = scrape_results(session, urls=rurls, fetcher=rf)
    assert summary.status == "succeeded"

    finishers = dict(session.execute(
        select(RaceResult.horse_id, RaceResult.finish_order).where(RaceResult.race_id == RACE_ID)
    ).all())
    assert set(finishers) == {"nk:H001", "nk:H002"}   # no result row for cancelled H003
    assert finishers["nk:H001"] == 1


def test_backfill_does_not_overwrite_jravan(session):
    fetcher, urls = fixture_fetcher("entries")
    scrape_entries(session, urls=urls, fetcher=fetcher)
    # seed a JRA-VAN result with a DIFFERENT finish_order
    session.add(RaceResult(race_id=RACE_ID, horse_id="nk:H001", finish_order=5,
                           result_status=ResultStatus.FINISHED))
    session.commit()

    rf, rurls = fixture_fetcher("results")   # netkeiba says H001 finished 1st
    scrape_results(session, urls=rurls, fetcher=rf)

    fo = session.scalar(
        select(RaceResult.finish_order).where(
            RaceResult.race_id == RACE_ID, RaceResult.horse_id == "nk:H001"
        )
    )
    assert fo == 5  # existing JRA-VAN row untouched (insert-only)
