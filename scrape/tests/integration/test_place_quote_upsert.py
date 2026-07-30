"""Phase 0-2: persist the 複勝 quote range without disturbing win odds or real dividends.

The hazard this pins down: `exotic_odds` is single-latest per (race_id, bet_type, selection) and
now holds REAL DIVIDENDS. Writing pre-race place quotes there would overwrite settled dividends
with prices. So the quote goes to race_horses.place_odds_* and MUST leave exotic_odds untouched.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal

import pytest
from horseracing_db.enums import BetType, CoverageScope, EntryStatus, ResultStatus
from horseracing_db.models import ExoticOdds, Horse, Race, RaceHorse, RaceResult
from sqlalchemy import func, select

from horseracing_scrape.parse.odds import parse_odds
from horseracing_scrape.upsert import update_odds

pytestmark = pytest.mark.integration

RID = "202406050911"
JST = datetime.timezone(datetime.timedelta(hours=9))


def _seed(session, *, n: int = 3, with_result: bool = False, win_odds: Decimal | None = None):
    session.merge(Race(race_id=RID, race_number=11, race_date=datetime.date(2024, 12, 28),
                       venue_code="06"))
    for i in range(1, n + 1):
        hid = f"nk:h{i}"
        session.merge(Horse(horse_id=hid, horse_name=hid))
        session.merge(RaceHorse(race_id=RID, horse_id=hid, horse_number=i,
                                entry_status=EntryStatus.STARTED, odds=win_odds))
        if with_result:
            session.merge(RaceResult(race_id=RID, horse_id=hid, finish_order=i,
                                     result_status=ResultStatus.FINISHED))
    session.commit()


def _payload(place: dict, *, official: str | None = "2024-12-28 15:50:17", win: dict | None = None):
    win = win or {f"{i:02d}": [f"{i * 2}.0", "0.0", f"{i}"] for i in range(1, 4)}
    data: dict = {"odds": {"1": win, "2": place}}
    if official is not None:
        data["official_datetime"] = official
    return parse_odds(json.dumps({"status": "result", "data": data}), RID)


FULL_PLACE = {"01": ["1.5", "2.1", "1"], "02": ["2.4", "3.9", "2"], "03": ["5.5", "9.3", "3"]}


def _quotes(session):
    return {
        n: (lo, hi, pop)
        for n, lo, hi, pop in session.execute(
            select(RaceHorse.horse_number, RaceHorse.place_odds_low,
                   RaceHorse.place_odds_high, RaceHorse.place_popularity)
            .where(RaceHorse.race_id == RID).order_by(RaceHorse.horse_number)
        )
    }


def test_place_quote_is_stored_with_both_ends_and_provenance(session):
    _seed(session)
    update_odds(session, RID, _payload(FULL_PLACE))
    session.commit()

    q = _quotes(session)
    assert q[1] == (Decimal("1.5"), Decimal("2.1"), 1)
    assert q[3] == (Decimal("5.5"), Decimal("9.3"), 3)

    race = session.get(Race, RID)
    assert race.place_odds_official_at == datetime.datetime(2024, 12, 28, 15, 50, 17, tzinfo=JST)
    assert race.place_odds_observed_at is not None
    assert race.place_odds_official_at != race.place_odds_observed_at, (
        "source-effective time and our observation time are different facts"
    )


def test_partial_field_skips_the_whole_race(session):
    """A cross-pool comparison needs the entire field priced at one instant, so a gap must skip
    the race rather than store a half-priced field."""
    _seed(session)
    incomplete = {"01": ["1.5", "2.1", "1"], "02": ["2.4", "3.9", "2"]}  # 馬番3 missing
    c = update_odds(session, RID, _payload(incomplete))
    session.commit()

    assert all(v == (None, None, None) for v in _quotes(session).values())
    assert c.skipped >= 1
    assert session.get(Race, RID).place_odds_official_at is None


def test_older_official_at_never_walks_the_quote_backwards(session):
    _seed(session)
    update_odds(session, RID, _payload(FULL_PLACE, official="2024-12-28 15:50:17"))
    session.commit()

    stale = {"01": ["9.9", "9.9", "9"], "02": ["9.9", "9.9", "9"], "03": ["9.9", "9.9", "9"]}
    update_odds(session, RID, _payload(stale, official="2024-12-28 10:00:00"))
    session.commit()

    assert _quotes(session)[1] == (Decimal("1.5"), Decimal("2.1"), 1)
    assert session.get(Race, RID).place_odds_official_at.astimezone(JST).hour == 15


def test_newer_official_at_overwrites(session):
    _seed(session)
    update_odds(session, RID, _payload(FULL_PLACE, official="2024-12-28 10:00:00"))
    session.commit()

    newer = {"01": ["1.2", "1.4", "1"], "02": ["2.4", "3.9", "2"], "03": ["5.5", "9.3", "3"]}
    update_odds(session, RID, _payload(newer, official="2024-12-28 15:50:17"))
    session.commit()

    assert _quotes(session)[1] == (Decimal("1.2"), Decimal("1.4"), 1)


def test_absent_place_group_leaves_existing_quote_intact(session):
    _seed(session)
    update_odds(session, RID, _payload(FULL_PLACE))
    session.commit()

    win_only = parse_odds(json.dumps({"status": "result", "data": {"odds": {
        "1": {"01": ["2.0", "0.0", "1"], "02": ["4.0", "0.0", "2"], "03": ["6.0", "0.0", "3"]}
    }}}), RID)
    update_odds(session, RID, win_only)
    session.commit()

    assert _quotes(session)[1] == (Decimal("1.5"), Decimal("2.1"), 1)


def test_real_dividends_are_untouched_by_quote_ingestion(session):
    """The regression that motivated a new column instead of reusing exotic_odds."""
    _seed(session, with_result=True)
    session.add(ExoticOdds(race_id=RID, bet_type=BetType.PLACE, selection=[1],
                           odds=Decimal("81.2"), coverage_scope=CoverageScope.PARTIAL,
                           source="netkeiba"))
    session.commit()

    update_odds(session, RID, _payload(FULL_PLACE))
    session.commit()

    div = session.scalars(select(ExoticOdds).where(ExoticOdds.race_id == RID)).all()
    assert len(div) == 1
    assert div[0].odds == Decimal("81.2"), "settled dividend must survive quote ingestion"
    assert session.scalar(select(func.count()).select_from(ExoticOdds)) == 1
    # and the quote landed in its own column, distinct from the dividend
    assert _quotes(session)[1] == (Decimal("1.5"), Decimal("2.1"), 1)


def test_win_odds_protection_is_unchanged_by_the_place_addition(session):
    """update_odds' fill-if-null guard for finalized races (JRA-VAN final odds) must be intact."""
    _seed(session, with_result=True, win_odds=Decimal("3.3"))
    update_odds(session, RID, _payload(FULL_PLACE))
    session.commit()

    odds = session.scalars(
        select(RaceHorse.odds).where(RaceHorse.race_id == RID).order_by(RaceHorse.horse_number)
    ).all()
    assert odds[0] == Decimal("3.3"), "existing (JRA-VAN) win odds must never be clobbered"
    # place quotes have no such value to protect and are written even post-result
    assert _quotes(session)[1] == (Decimal("1.5"), Decimal("2.1"), 1)


def test_reingesting_the_same_payload_is_a_no_op(session):
    _seed(session)
    update_odds(session, RID, _payload(FULL_PLACE))
    session.commit()
    before = _quotes(session)

    update_odds(session, RID, _payload(FULL_PLACE))
    session.commit()

    assert _quotes(session) == before


def test_half_range_cannot_reach_the_database(session):
    """Parser drops a one-sided range to (None, None); the CHECK constraint is the backstop."""
    _seed(session)
    half = {"01": ["1.5", "---.-", "1"], "02": ["2.4", "3.9", "2"], "03": ["5.5", "9.3", "3"]}
    update_odds(session, RID, _payload(half))
    session.commit()
    # 馬番1 has no usable range -> the field is incomplete -> whole race skipped
    assert all(v == (None, None, None) for v in _quotes(session).values())
