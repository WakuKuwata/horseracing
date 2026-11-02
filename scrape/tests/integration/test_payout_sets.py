"""176 T026: payout blocks are replaced as SETS and adopted by source priority (FR-015)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from horseracing_db.enums import BetType, Source
from horseracing_db.models import ExoticOdds, OfficialWinPayout
from sqlalchemy import select

from horseracing_scrape.models import ScrapedExoticOdds, ScrapedExoticRow, ScrapedWinPayout
from horseracing_scrape.upsert import (
    OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
    OFFICIAL_WIN_PAYOUT_SOURCE_NETKEIBA,
    replace_exotic_dividends,
    upsert_exotic_odds,
    upsert_official_win_payouts,
)
from tests._seed176 import KEY, RID, T1, T2, race_prov, seed_race

pytestmark = pytest.mark.integration
JRA, NK = Source.JRA_OFFICIAL, Source.NETKEIBA


def _win_rows(session):
    session.expire_all()
    return {
        r.horse_number: (r.payout_yen, r.source)
        for r in session.scalars(select(OfficialWinPayout).where(OfficialWinPayout.race_id == RID))
    }


def _exotic(session, bet_type):
    session.expire_all()
    return {
        tuple(r.selection): (r.odds, r.source)
        for r in session.scalars(
            select(ExoticOdds).where(ExoticOdds.race_id == RID, ExoticOdds.bet_type == bet_type)
        )
    }


def test_official_win_payout_confirmation_moves_the_source_only(session):
    seed_race(session, n=3, with_result=True)
    out = upsert_official_win_payouts(session, RID, [ScrapedWinPayout(2, 460)], observed_at=T1)
    session.commit()
    assert out.inserted == 1 and _win_rows(session) == {
        2: (460, OFFICIAL_WIN_PAYOUT_SOURCE_NETKEIBA)
    }
    assert race_prov(session)["win_payouts"][0] == NK
    out = upsert_official_win_payouts(
        session,
        RID,
        [ScrapedWinPayout(2, 460)],
        observed_at=T2,
        source=OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
    )
    session.commit()
    assert out.confirmed == 1 and out.written == 0
    assert _win_rows(session) == {2: (460, OFFICIAL_WIN_PAYOUT_SOURCE_JRA)}
    assert race_prov(session)["win_payouts"][0] == JRA
    # netkeiba can no longer touch the block, even with a different value
    out = upsert_official_win_payouts(session, RID, [ScrapedWinPayout(2, 450)], observed_at=T2)
    session.commit()
    assert out.skipped_official == 1 and _win_rows(session)[2] == (
        460,
        OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
    )


def test_dead_heat_and_correction_replace_the_set(session):
    seed_race(session, n=3, with_result=True)
    upsert_official_win_payouts(
        session,
        RID,
        [ScrapedWinPayout(2, 460)],
        observed_at=T1,
        source=OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
    )
    session.commit()
    out = upsert_official_win_payouts(
        session,
        RID,
        [ScrapedWinPayout(1, 380), ScrapedWinPayout(3, 510)],
        observed_at=T2,
        source=OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
    )
    session.commit()
    assert out.removed == 1 and out.inserted == 2
    assert set(_win_rows(session)) == {1, 3}


def test_exotic_dividends_set_replacement_and_netkeiba_skip(session):
    seed_race(session, n=3, with_result=True)
    # netkeiba first
    upsert_exotic_odds(
        session,
        RID,
        ScrapedExoticOdds(
            KEY,
            (
                ScrapedExoticRow(BetType.QUINELLA, (1, 2), 2.8),
                ScrapedExoticRow(BetType.QUINELLA, (2, 3), 9.9),
            ),
        ),
        source=NK,
        observed_at=T1,
    )
    session.commit()
    assert set(_exotic(session, BetType.QUINELLA)) == {(1, 2), (2, 3)}
    # official says the winning quinella is 1-2 at 280 yen only
    c = replace_exotic_dividends(session, RID, BetType.QUINELLA, {(1, 2): 280}, observed_at=T2)
    session.commit()
    rows = _exotic(session, BetType.QUINELLA)
    assert set(rows) == {(1, 2)} and rows[(1, 2)] == (
        Decimal("2.8"),
        JRA,
    )  # same value → source only
    assert c.extra.get("payout_rows_removed") == 1
    assert race_prov(session)["exotic_payouts"][0] == JRA
    # netkeiba afterwards is skipped
    c = upsert_exotic_odds(
        session,
        RID,
        ScrapedExoticOdds(KEY, (ScrapedExoticRow(BetType.QUINELLA, (2, 3), 9.9),)),
        source=NK,
        observed_at=T2,
    )
    session.commit()
    assert c.extra.get("skipped_official_payouts") == 1
    assert set(_exotic(session, BetType.QUINELLA)) == {(1, 2)}


def test_exotic_dividend_value_change_is_an_update(session):
    seed_race(session, n=3, with_result=True)
    replace_exotic_dividends(session, RID, BetType.TRIO, {(1, 2, 3): 2980}, observed_at=T1)
    c = replace_exotic_dividends(session, RID, BetType.TRIO, {(1, 2, 3): 3080}, observed_at=T2)
    session.commit()
    assert c.extra.get("payout_rows_written") == 1
    assert _exotic(session, BetType.TRIO)[(1, 2, 3)][0] == Decimal("30.8")
