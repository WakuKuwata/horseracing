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
    scrape_entries(session, urls=eurls, fetcher=ef, complete_profiles_after=False)
    of, ourls = real_odds_fetcher()
    summary = scrape_odds(session, urls=ourls, fetcher=of)
    assert summary.status == "succeeded"
    odds, pop = _row(session, H_NUM1)  # 馬番1
    assert odds == Decimal("19.1") and pop == 6


def test_finished_race_fills_null_odds_but_protects_existing(session):
    # finished race: an EXISTING (JRA-VAN) odds value must be protected, while a horse whose odds
    # is still NULL gets filled from netkeiba's confirmed odds (netkeiba-only finished races).
    ef, eurls = real_entries_fetcher()
    scrape_entries(session, urls=eurls, fetcher=ef, complete_profiles_after=False)
    session.add(RaceResult(race_id=REAL_RID, horse_id=H_NUM1, finish_order=1,
                           result_status=ResultStatus.FINISHED))
    session.execute(RaceHorse.__table__.update().where(
        RaceHorse.race_id == REAL_RID, RaceHorse.horse_id == H_NUM1).values(odds=Decimal("9.9")))
    session.commit()

    of, ourls = real_odds_fetcher()
    summary = scrape_odds(session, urls=ourls, fetcher=of)
    assert summary.skipped == 1            # only H_NUM1 (already had odds) protected
    odds, _ = _row(session, H_NUM1)
    assert odds == Decimal("9.9")          # existing (JRA-VAN) final odds untouched
    # a different horse whose odds was NULL is now filled from netkeiba confirmed odds
    assert summary.written >= 1
