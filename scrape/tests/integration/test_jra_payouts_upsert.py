"""176 T071: official payouts as sets per bet type (FR-015, contracts/precedence.md §6)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import BetType, Source
from horseracing_db.models import ExoticOdds, OfficialWinPayout
from sqlalchemy import func, select

from horseracing_scrape.jra.models import JraPayoutBlock, JraPayoutLine, JraPayoutSet
from horseracing_scrape.jra.parse.payouts import parse_payouts_day
from horseracing_scrape.jra.upsert_jra import upsert_jra_payouts
from horseracing_scrape.models import (
    ScrapedExoticOdds,
    ScrapedExoticRow,
    ScrapedRaceKey,
    ScrapedWinPayout,
)
from horseracing_scrape.upsert import upsert_exotic_odds, upsert_official_win_payouts
from tests._seed176 import race_prov
from tests._seed_results176 import PAYOUTS, D, seed_from_results
from tests.jra_fixtures import fetched_at, jra_text

pytestmark = pytest.mark.integration
JRA, NK = Source.JRA_OFFICIAL, Source.NETKEIBA
R1 = "202605041001"
KEY1 = ScrapedRaceKey(2026, "05", 4, 10, 1)
OBS = fetched_at(PAYOUTS)


def _page():
    return parse_payouts_day(jra_text(PAYOUTS), expected_date=D)


def _set(race_id: str) -> JraPayoutSet:
    return next(b.payload for b in _page().published() if b.race_id == race_id)


def _exotic(session, rid=R1, bet_type=None):
    q = select(ExoticOdds).where(ExoticOdds.race_id == rid)
    if bet_type:
        q = q.where(ExoticOdds.bet_type == bet_type)
    return {
        (r.bet_type, tuple(r.selection)): (r.odds, r.source, r.coverage_scope)
        for r in session.scalars(q)
    }


def test_full_day_writes_win_and_six_exotic_types(session):
    seed_from_results(session)
    total = {}
    for b in _page().published():
        c = upsert_jra_payouts(session, b.race_id, b.payload, observed_at=OBS)
        for k, v in c.extra.items():
            total[k] = total.get(k, 0) + v
    session.commit()
    assert total["payout_complete"] == 12 * 7 and total["not_mapped_bet_types"] == 12
    assert session.scalar(select(func.count()).select_from(OfficialWinPayout)) == 12
    win = session.get(OfficialWinPayout, (R1, 12))
    assert win.payout_yen == 460 and win.source == "jra_official_result"
    ex = _exotic(session)
    assert ex[(BetType.TRIFECTA, (12, 11, 6))] == (Decimal("156.40"), JRA, "partial")
    assert ex[(BetType.EXACTA, (12, 11))][0] == Decimal("6.80")
    assert ex[(BetType.QUINELLA, (11, 12))][0] == Decimal("2.80")
    assert (
        len([k for k in ex if k[0] == BetType.WIDE]) == 3
        and len([k for k in ex if k[0] == BetType.PLACE]) == 3
    )
    assert not any(k[0] == "bracket_quinella" for k in ex)
    pv = race_prov(session, R1)
    assert pv["win_payouts"][0] == JRA and pv["exotic_payouts"][0] == JRA


def test_rerun_changes_nothing(session):
    seed_from_results(session, race_ids=[R1])
    upsert_jra_payouts(session, R1, _set(R1), observed_at=OBS)
    session.commit()
    before = _exotic(session)
    c = upsert_jra_payouts(session, R1, _set(R1), observed_at=OBS + datetime.timedelta(hours=1))
    session.commit()
    assert _exotic(session) == before and c.written == 0


def test_netkeiba_same_value_is_confirmed_and_later_netkeiba_is_skipped(session):
    seed_from_results(session, race_ids=[R1])
    upsert_official_win_payouts(session, R1, [ScrapedWinPayout(12, 460)], observed_at=OBS)
    upsert_exotic_odds(
        session, R1, ScrapedExoticOdds(KEY1, (ScrapedExoticRow(BetType.QUINELLA, (11, 12), 2.8),))
    )
    session.commit()
    c = upsert_jra_payouts(session, R1, _set(R1), observed_at=OBS)
    session.commit()
    assert c.extra.get("win_payout_confirmed") == 1
    assert session.get(OfficialWinPayout, (R1, 12)).source == "jra_official_result"
    assert _exotic(session, bet_type=BetType.QUINELLA)[(BetType.QUINELLA, (11, 12))][1] == JRA
    # afterwards netkeiba can no longer write the payouts of this race
    out = upsert_official_win_payouts(session, R1, [ScrapedWinPayout(12, 470)], observed_at=OBS)
    nk = upsert_exotic_odds(
        session, R1, ScrapedExoticOdds(KEY1, (ScrapedExoticRow(BetType.QUINELLA, (11, 12), 9.9),))
    )
    session.commit()
    assert out.skipped_official == 1 and nk.extra.get("skipped_official_payouts") == 1
    assert session.get(OfficialWinPayout, (R1, 12)).payout_yen == 460


def test_correction_dead_heat_and_block_states(session):
    seed_from_results(session, race_ids=[R1])
    upsert_jra_payouts(session, R1, _set(R1), observed_at=OBS)
    session.commit()
    trio_before = _exotic(session, bet_type=BetType.TRIO)
    corrected = JraPayoutSet(
        {
            BetType.WIN: JraPayoutBlock(
                BetType.WIN,
                "complete",
                (JraPayoutLine((11,), 300, 1), JraPayoutLine((12,), 250, 2)),
            ),
            BetType.QUINELLA: JraPayoutBlock(
                BetType.QUINELLA, "complete", (JraPayoutLine((6, 11), 900, 3),)
            ),
            BetType.TRIO: JraPayoutBlock(BetType.TRIO, "not_published"),
            BetType.EXACTA: JraPayoutBlock(BetType.EXACTA, "parse_failed", error="boom"),
            BetType.WIDE: JraPayoutBlock(BetType.WIDE, "refund_only", refunds=(3,)),
        }
    )
    c = upsert_jra_payouts(session, R1, corrected, observed_at=OBS + datetime.timedelta(hours=2))
    session.commit()
    # dead heat: two win rows; the old winner set is replaced
    wins = {
        r.horse_number: r.payout_yen
        for r in session.scalars(select(OfficialWinPayout).where(OfficialWinPayout.race_id == R1))
    }
    assert wins == {11: 300, 12: 250}
    # quinella set replaced (old row deleted)
    assert set(_exotic(session, bet_type=BetType.QUINELLA)) == {(BetType.QUINELLA, (6, 11))}
    # not_published / parse_failed / refund_only leave the stored rows as they were
    assert _exotic(session, bet_type=BetType.TRIO) == trio_before
    assert len(_exotic(session, bet_type=BetType.EXACTA)) == 1
    assert len(_exotic(session, bet_type=BetType.WIDE)) == 3
    assert c.extra.get("payout_parse_failed") == 1 and c.extra.get("payout_not_published") == 1
    assert c.extra.get("payout_refund_only") == 1 and c.extra.get("refunds") == 1
    assert any("parse_failed" in m for m in c.error_messages)


def test_race_not_in_db_and_pre_netkeiba_era(session):
    c = upsert_jra_payouts(session, R1, _set(R1), observed_at=OBS)
    assert c.extra.get("payout_race_not_in_db") == 1
    from horseracing_db.models import Race

    session.merge(
        Race(
            race_id="202405040101",
            race_date=datetime.date(2024, 10, 31),
            race_number=1,
            venue_code="05",
        )
    )
    session.commit()
    c = upsert_jra_payouts(session, "202405040101", _set(R1), observed_at=OBS)
    session.commit()
    assert c.extra.get("skipped_pre_netkeiba_era") == 1
    assert session.scalar(select(func.count()).select_from(ExoticOdds)) == 0
