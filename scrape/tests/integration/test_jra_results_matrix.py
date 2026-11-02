"""176 T072: the two result routes in both orders (SC-010)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import ResultStatus, Source
from horseracing_db.models import RaceResult
from sqlalchemy import func, select

from horseracing_scrape.jra.upsert_jra import upsert_jra_results
from horseracing_scrape.models import ResolvedResultRow
from horseracing_scrape.upsert import PER_HORSE_LEGACY, _insert_results
from tests._seed176 import race_prov
from tests._seed_results176 import results_observed, seed_from_results

pytestmark = pytest.mark.integration
R1 = "202605041001"


def _rows(session):
    session.expire_all()
    return {
        r.horse_id: (r.finish_order, r.result_status, r.finish_time, r.corner_orders)
        for r in session.scalars(select(RaceResult).where(RaceResult.race_id == R1))
    }


def test_official_first_then_netkeiba_adds_nothing_and_changes_nothing(session):
    page = seed_from_results(session, race_ids=[R1])
    upsert_jra_results(session, page, observed_at=results_observed())
    session.commit()
    before = _rows(session)
    r1 = next(b.payload for b in page.published() if b.race_id == R1)
    nk_rows = [
        ResolvedResultRow(
            r.horse_id,
            r.finish_order,
            r.result_status,
            r.finish_time,
            r.last_3f,
            ("1", "1", "1", "1"),
        )
        for r in r1.rows
        if r.result_status in ResultStatus.ALL
    ]
    c = _insert_results(session, R1, nk_rows, mode=PER_HORSE_LEGACY, source=Source.NETKEIBA)
    session.commit()
    after = _rows(session)
    # rows already exist → nothing inserted; corner orders were filled by the official route, so
    # the NULL-only fill has nothing to fill
    assert after == before
    assert all(v[3] is not None for v in after.values())
    assert race_prov(session, R1)["results"][0] == Source.JRA_OFFICIAL
    assert c.extra.get("results_inserted_races") is None


def test_netkeiba_partial_first_then_official_keeps_netkeiba_rows(session):
    page = seed_from_results(session, race_ids=[R1])
    r1 = next(b.payload for b in page.published() if b.race_id == R1)
    two = [
        ResolvedResultRow(r.horse_id, r.finish_order, r.result_status, r.finish_time)
        for r in r1.rows[:2]
    ]
    _insert_results(session, R1, two, mode=PER_HORSE_LEGACY, source=Source.NETKEIBA)
    session.commit()
    c = upsert_jra_results(session, page, observed_at=results_observed())
    session.commit()
    assert c.extra.get("results_existing_races") == 1 and not c.extra.get("results_mismatch")
    assert session.scalar(select(func.count()).select_from(RaceResult)) == 2
    assert race_prov(session, R1)["results"][0] == Source.NETKEIBA
    # the netkeiba route can still complete its own race per horse
    rest = [
        ResolvedResultRow(r.horse_id, r.finish_order, r.result_status, r.finish_time)
        for r in r1.rows
        if r.result_status in ResultStatus.ALL
    ]
    _insert_results(session, R1, rest, mode=PER_HORSE_LEGACY, source=Source.NETKEIBA)
    session.commit()
    assert session.scalar(select(func.count()).select_from(RaceResult)) == len(rest)
