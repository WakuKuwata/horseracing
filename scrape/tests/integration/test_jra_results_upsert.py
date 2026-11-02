"""176 T070: the official results page → race_results (race-atomic), body weights, laps; the
result page's popularity is never stored; netkeiba-era guard."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus, Source
from horseracing_db.models import Horse, OfficialWinPayout, RaceHorse, RaceLaps, RaceResult
from sqlalchemy import func, select

from horseracing_scrape.jra.upsert_jra import first_3f_from_laps, upsert_jra_results
from horseracing_scrape.models import ParseError, ResolvedResultRow
from horseracing_scrape.upsert import PER_HORSE_LEGACY, _insert_results
from tests._seed176 import horse_prov, race_prov
from tests._seed_results176 import RESULTS, D, results_observed, results_page, seed_from_results
from tests.jra_fixtures import jra_text

pytestmark = pytest.mark.integration
JRA, NK = Source.JRA_OFFICIAL, Source.NETKEIBA
R1 = "202605041001"
R6 = "202605041006"  # has one 取消


def _count(session, model, **where):
    q = select(func.count()).select_from(model)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return session.scalar(q)


def test_clean_day_inserts_every_race_atomically(session):
    page = seed_from_results(session)
    pops_before = {
        (rh.race_id, rh.horse_id): (rh.popularity, rh.odds)
        for rh in session.scalars(select(RaceHorse))
    }
    c = upsert_jra_results(session, page, observed_at=results_observed())
    session.commit()
    assert c.extra.get("results_inserted_races") == 12 and c.errors == 0
    finished = sum(
        1 for b in page.published() for r in b.payload.rows if r.result_status in ResultStatus.ALL
    )
    assert _count(session, RaceResult) == finished
    assert race_prov(session, R1)["results"][0] == JRA
    # popularity / odds are never written by the results route (user decision 2026-11-02)
    session.expire_all()
    assert {
        (rh.race_id, rh.horse_id): (rh.popularity, rh.odds)
        for rh in session.scalars(select(RaceHorse))
    } == pops_before
    assert c.extra.get("popularity_mismatch", 0) > 0
    # no final-odds / payout side effect
    assert _count(session, OfficialWinPayout) == 0
    # body weights written under the pair rule with official provenance
    rh = session.scalars(select(RaceHorse).where(RaceHorse.race_id == R1)).first()
    assert rh.weight is not None
    assert horse_prov(session, R1)[(rh.horse_id, "body_weight")][0] == JRA
    # the 取消 horse is corrected to cancelled and gets no result row
    r6 = next(b.payload for b in page.published() if b.race_id == R6)
    cancelled = next(r.horse_id for r in r6.rows if r.result_status == EntryStatus.CANCELLED)
    assert session.get(RaceHorse, (R6, cancelled)).entry_status == EntryStatus.CANCELLED
    assert session.get(RaceResult, (R6, cancelled)) is None
    assert c.extra.get("status_transitions") == 1
    # laps: inserted once, テン3F derived only for multiples of 200m
    lap = session.get(RaceLaps, R1)
    assert lap.source == JRA and len(lap.lap_times) == 8 and lap.pace_last_3f == Decimal("38.1")
    assert lap.pace_first_3f == Decimal(str(first_3f_from_laps(1600, lap.lap_times)))
    assert session.get(RaceLaps, R6).pace_first_3f is None  # 2100m starts with a 100m lap
    assert c.extra.get("laps_inserted") == 12
    # winner's time and the time diff are computed by the shared writer
    winner = session.scalars(
        select(RaceResult).where(RaceResult.race_id == R1, RaceResult.finish_order == 1)
    ).one()
    assert winner.finish_time is not None and winner.finish_time_diff == datetime.timedelta(0)


def test_rerun_is_a_noop_and_laps_are_kept(session):
    page = seed_from_results(session, race_ids=[R1])
    upsert_jra_results(session, page, observed_at=results_observed())
    session.commit()
    n = _count(session, RaceResult)
    c = upsert_jra_results(session, page, observed_at=results_observed())
    session.commit()
    assert _count(session, RaceResult) == n
    assert c.extra.get("results_existing_races") == 1 and not c.extra.get("results_mismatch")
    assert c.extra.get("laps_existing") == 1


def test_existing_netkeiba_row_blocks_the_official_insert_and_reports_mismatch(session):
    page = seed_from_results(session, race_ids=[R1])
    r1 = next(b.payload for b in page.published() if b.race_id == R1)
    first = r1.rows[0]
    # netkeiba wrote one horse with a different 着順
    _insert_results(
        session,
        R1,
        [ResolvedResultRow(first.horse_id, 2, ResultStatus.FINISHED, "1:35.0")],
        mode=PER_HORSE_LEGACY,
        source=NK,
    )
    session.commit()
    c = upsert_jra_results(session, page, observed_at=results_observed())
    session.commit()
    assert c.extra.get("results_existing_races") == 1
    assert c.extra.get("results_mismatch") == 1
    assert any(first.horse_id in m for m in c.error_messages)
    assert _count(session, RaceResult, race_id=R1) == 1


def test_collision_quarantines_the_race(session):
    page = seed_from_results(session, race_ids=[R1])
    r1 = next(b.payload for b in page.published() if b.race_id == R1)
    # a surrogate row already holds 馬番 1
    session.merge(Horse(horse_id="nk:x", horse_name="別馬", birth_year=2022))
    session.merge(
        RaceHorse(
            race_id=R1,
            horse_id="nk:x",
            horse_number=r1.rows[0].horse_number,
            entry_status=EntryStatus.STARTED,
        )
    )
    session.commit()
    c = upsert_jra_results(session, page, observed_at=results_observed())
    session.commit()
    assert c.extra.get("quarantined_collision") == 1
    assert _count(session, RaceResult) == 0


def test_race_not_in_db_and_pre_netkeiba_era_write_nothing(session):
    c = upsert_jra_results(session, results_page(), observed_at=results_observed())
    session.commit()
    assert c.extra.get("results_race_not_in_db") == 12 and _count(session, RaceResult) == 0
    old_html = jra_text(RESULTS).replace("2026年10月31日（土曜）", "2024年10月31日（木曜）")
    old = results_page(old_html, date=datetime.date(2024, 10, 31))
    c = upsert_jra_results(session, old, observed_at=results_observed())
    session.commit()
    assert c.extra.get("skipped_pre_netkeiba_era") == 12
    assert _count(session, RaceResult) == 0 and _count(session, RaceLaps) == 0


def test_date_guard_in_parser_rejects_another_day():
    with pytest.raises(ParseError):
        results_page(date=D + datetime.timedelta(days=1))
