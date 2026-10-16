"""Feature 140 (T003/T004): provenance of the latest win quote — races.win_odds_official_at /
win_odds_observed_at.

What these pin down (plan 0.2): on a result-pending race a payload with at least one valid win odds
writes the provenance EVEN WHEN THE PRICE DID NOT MOVE (a fresh quote at the same price is a fresh
observation), a stored NULL accepts anything, the same instant is accepted, a payload without
``official_at`` moves only ``observed_at``, a payload OLDER than the stored ``official_at`` writes
neither the odds nor the provenance (``skipped``), an entries re-ingest leaves it alone, and the
settled fill-if-null path never touches it. ``apply_final_odds`` (the result page) sets
``observed_at`` and NULLs ``official_at`` only when it rewrote at least one row.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult
from sqlalchemy import select

from horseracing_scrape import pipeline
from horseracing_scrape.parse.odds import parse_odds
from horseracing_scrape.parse.results import parse_results
from horseracing_scrape.upsert import apply_final_odds, update_odds
from tests._synth import REAL_RID, real_entries_fetcher
from tests.conftest import real_fixture

pytestmark = pytest.mark.integration

RID = "202406050911"
JST = datetime.timezone(datetime.timedelta(hours=9))
T1 = "2024-12-28 10:00:00"
T2 = "2024-12-28 15:50:17"
T0 = "2024-12-28 09:00:00"


def _jst(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)


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


def _payload(*, official: str | None = T2, win: dict | None = None, n: int = 3):
    win = win or {f"{i:02d}": [f"{i * 2}.0", "0.0", f"{i}"] for i in range(1, n + 1)}
    data: dict = {"odds": {"1": win}}
    if official is not None:
        data["official_datetime"] = official
    return parse_odds(json.dumps({"status": "result", "data": data}), RID)


def _prov(session, rid: str = RID) -> tuple:
    session.expire_all()
    race = session.get(Race, rid)
    return race.win_odds_official_at, race.win_odds_observed_at


def _odds(session, rid: str = RID) -> list[Decimal | None]:
    return list(session.scalars(select(RaceHorse.odds).where(RaceHorse.race_id == rid)
                                .order_by(RaceHorse.horse_number)))


# --- update_odds (the live quote) -------------------------------------------------------------


def test_pending_race_writes_odds_and_provenance(session):
    _seed(session)
    before = datetime.datetime.now(datetime.UTC)
    c = update_odds(session, RID, _payload(official=T2))
    session.commit()

    assert c.written == 3 and c.skipped == 0
    assert _odds(session) == [Decimal("2.0"), Decimal("4.0"), Decimal("6.0")]
    official, observed = _prov(session)
    assert official == _jst(T2)
    assert official.tzinfo is not None and observed.tzinfo is not None
    assert observed >= before
    assert official != observed, "source-declared time and our observation time are different facts"
    # the place provenance is a different fact: untouched by a win-only payload
    race = session.get(Race, RID)
    assert race.place_odds_official_at is None and race.place_odds_observed_at is None


def test_same_price_again_is_a_new_observation(session):
    """codex: a value that did not move is still a fresh quote — the provenance must advance."""
    _seed(session)
    update_odds(session, RID, _payload(official=T1))
    session.commit()
    official_1, observed_1 = _prov(session)

    c = update_odds(session, RID, _payload(official=T2))  # same prices, later quote
    session.commit()

    assert c.written == 3                     # UPDATE touched the rows (same values)
    assert _odds(session) == [Decimal("2.0"), Decimal("4.0"), Decimal("6.0")]
    official_2, observed_2 = _prov(session)
    assert (official_1, official_2) == (_jst(T1), _jst(T2))
    assert observed_2 > observed_1


def test_stored_null_accepts_any_official_at(session):
    _seed(session)
    assert _prov(session) == (None, None)
    update_odds(session, RID, _payload(official=T0))   # any time at all when nothing is stored
    session.commit()
    official, observed = _prov(session)
    assert official == _jst(T0) and observed is not None


def test_same_instant_is_accepted(session):
    _seed(session)
    update_odds(session, RID, _payload(official=T2))
    session.commit()
    _, observed_1 = _prov(session)

    moved = {"01": ["3.0", "0.0", "1"], "02": ["4.0", "0.0", "2"], "03": ["6.0", "0.0", "3"]}
    c = update_odds(session, RID, _payload(official=T2, win=moved))
    session.commit()

    assert c.skipped == 0
    assert _odds(session)[0] == Decimal("3.0")
    official, observed_2 = _prov(session)
    assert official == _jst(T2) and observed_2 > observed_1


def test_future_official_at_is_simply_newer(session):
    _seed(session)
    update_odds(session, RID, _payload(official=T2))
    session.commit()
    future = "2031-01-01 00:00:00"
    update_odds(session, RID, _payload(official=future))
    session.commit()
    official, _ = _prov(session)
    assert official == _jst(future)


def test_tz_comparison_uses_the_instant_not_the_wall_clock(session):
    """The payload is JST; the stored value comes back in UTC. 15:50 JST == 06:50 UTC, and a
    payload at 07:00 JST (= 22:00 UTC the day before) is OLDER although its clock reads later
    than the stored UTC wall-clock of 06:50."""
    _seed(session)
    update_odds(session, RID, _payload(official=T2))        # 15:50:17 JST = 06:50:17 UTC
    session.commit()
    official, _ = _prov(session)
    assert official == datetime.datetime(2024, 12, 28, 6, 50, 17, tzinfo=datetime.UTC)

    c = update_odds(session, RID, _payload(official="2024-12-28 07:00:00",
                                           win={"01": ["9.9", "0.0", "1"]}))
    session.commit()
    assert c.skipped == 1 and c.written == 0
    assert _odds(session)[0] == Decimal("2.0")
    assert _prov(session)[0] == _jst(T2)


def test_older_official_at_writes_neither_odds_nor_provenance(session):
    _seed(session)
    update_odds(session, RID, _payload(official=T2))
    session.commit()
    official_before, observed_before = _prov(session)

    stale = {"01": ["9.9", "0.0", "9"], "02": ["9.9", "0.0", "9"], "03": ["9.9", "0.0", "9"]}
    c = update_odds(session, RID, _payload(official=T1, win=stale))
    session.commit()

    assert c.skipped == 3 and c.written == 0 and c.processed == 0
    assert _odds(session) == [Decimal("2.0"), Decimal("4.0"), Decimal("6.0")]
    assert _prov(session) == (official_before, observed_before)


def test_payload_without_official_at_moves_only_observed_at(session):
    _seed(session)
    update_odds(session, RID, _payload(official=T2))
    session.commit()
    official_1, observed_1 = _prov(session)

    c = update_odds(session, RID, _payload(official=None))
    session.commit()

    assert c.written == 3
    official_2, observed_2 = _prov(session)
    assert official_2 == official_1 == _jst(T2)   # kept: nothing to replace it with
    assert observed_2 > observed_1


def test_payload_without_official_at_on_a_fresh_race(session):
    _seed(session)
    update_odds(session, RID, _payload(official=None))
    session.commit()
    official, observed = _prov(session)
    assert official is None and observed is not None


def test_payload_without_any_valid_odds_writes_no_provenance(session):
    _seed(session)
    c = update_odds(session, RID, _payload(win={"01": ["0.0", "0.0", "1"],
                                                "02": ["---.-", "0.0", "2"]}))
    session.commit()
    assert c.written == 0
    assert _prov(session) == (None, None)


def test_payload_matching_no_stored_horse_writes_no_provenance(session):
    """Review a3-scrape-db-1: the stored odds stay the OLD quote when the payload's 馬番 match no
    race_horses row (entries not ingested / renumbered field), so the provenance must not advance
    — otherwise a NEW observation time would describe an OLD price (and market_ev would read a
    too-small horizon post_time − observed_at for that race)."""
    _seed(session, win_odds=Decimal("5.0"))
    update_odds(session, RID, _payload(official=T1))      # a real quote at T1 first
    session.commit()
    before = _prov(session)
    assert before[0] == _jst(T1)

    unmatched = {"07": ["9.9", "0.0", "1"], "08": ["3.3", "0.0", "2"]}   # no such 馬番 (1..3)
    c = update_odds(session, RID, _payload(official=T2, win=unmatched))
    session.commit()

    assert c.written == 0 and c.processed == 2
    assert _odds(session) == [Decimal("2.0"), Decimal("4.0"), Decimal("6.0")]   # the T1 quote
    assert _prov(session) == before                       # neither official_at nor observed_at moved

    # the next payload that matches rows is a new observation as usual
    c = update_odds(session, RID, _payload(official=T2))
    session.commit()
    assert c.written == 3
    official, observed = _prov(session)
    assert official == _jst(T2) and observed > before[1]


def test_entries_reingest_leaves_the_provenance_alone(session):
    ef, eurls = real_entries_fetcher()
    pipeline.scrape_entries(session, urls=eurls, fetcher=ef, complete_profiles_after=False)
    numbers = sorted(session.scalars(select(RaceHorse.horse_number)
                                     .where(RaceHorse.race_id == REAL_RID)))
    win = {f"{n:02d}": [f"{n + 1}.0", "0.0", f"{n}"] for n in numbers}
    update_odds(session, REAL_RID, _payload(official=T2, win=win))
    session.commit()
    before = _prov(session, REAL_RID)
    assert before[0] == _jst(T2)

    pipeline.scrape_entries(session, urls=eurls, fetcher=ef, complete_profiles_after=False)

    assert _prov(session, REAL_RID) == before
    assert session.scalar(select(RaceHorse.odds).where(
        RaceHorse.race_id == REAL_RID, RaceHorse.horse_number == numbers[0]
    )) == Decimal(f"{numbers[0] + 1}.0")


def test_settled_fill_if_null_never_touches_the_provenance(session):
    """After the result the quote endpoint only fills NULL odds — the final odds' provenance is
    apply_final_odds's business, so this path must leave win_odds_* exactly as it found them."""
    _seed(session, with_result=True, win_odds=None)
    stamp = datetime.datetime(2024, 12, 28, 7, 10, tzinfo=datetime.UTC)
    session.execute(Race.__table__.update().where(Race.race_id == RID)
                    .values(win_odds_observed_at=stamp, win_odds_official_at=None))
    session.commit()

    c = update_odds(session, RID, _payload(official=T2))
    session.commit()

    assert c.written == 3                                 # the NULLs were filled ...
    assert _odds(session) == [Decimal("2.0"), Decimal("4.0"), Decimal("6.0")]
    assert _prov(session) == (None, stamp)                # ... the provenance did not move


def test_settled_race_with_existing_odds_touches_nothing(session):
    _seed(session, with_result=True, win_odds=Decimal("3.3"))
    c = update_odds(session, RID, _payload(official=T2))
    session.commit()
    assert c.skipped == 3 and c.written == 0
    assert _prov(session) == (None, None)


# --- apply_final_odds (the result page) ---------------------------------------------------------

FINAL_RID = "202602011206"
FINAL_FIXTURE = "results_202602011206.html"
FINAL_OBSERVED = datetime.datetime(2026, 7, 19, 8, 0, tzinfo=datetime.UTC)


def _seed_final(session, *, pre_race: Decimal | None = Decimal("9.9")) -> list:
    rows = parse_results(real_fixture(FINAL_FIXTURE)).rows
    session.merge(Race(race_id=FINAL_RID, race_number=6, race_date=datetime.date(2026, 7, 19),
                       venue_code="02",
                       win_odds_official_at=datetime.datetime(2026, 7, 19, 6, 0,
                                                              tzinfo=datetime.UTC),
                       win_odds_observed_at=datetime.datetime(2026, 7, 19, 6, 1,
                                                              tzinfo=datetime.UTC)))
    for number, row in enumerate(rows, start=1):
        hid = f"nk:{row.netkeiba_horse_id}"
        session.merge(Horse(horse_id=hid, horse_name=hid))
        session.add(RaceHorse(race_id=FINAL_RID, horse_id=hid, horse_number=number,
                              entry_status=EntryStatus.STARTED, odds=pre_race, popularity=9))
    session.commit()
    return rows


def test_final_odds_set_observed_at_and_null_official_at(session):
    rows = _seed_final(session)
    out = apply_final_odds(session, FINAL_RID, rows, observed_at=FINAL_OBSERVED)
    session.commit()
    assert out.updated == 16
    assert _prov(session, FINAL_RID) == (None, FINAL_OBSERVED)


def test_final_odds_rerun_leaves_the_provenance_as_it_was(session):
    rows = _seed_final(session)
    apply_final_odds(session, FINAL_RID, rows, observed_at=FINAL_OBSERVED)
    session.commit()
    later = FINAL_OBSERVED + datetime.timedelta(days=1)

    again = apply_final_odds(session, FINAL_RID, rows, observed_at=later)
    session.commit()

    assert again.updated == 0 and again.unchanged == 16
    assert _prov(session, FINAL_RID) == (None, FINAL_OBSERVED)   # not `later`


def test_final_odds_pre_netkeiba_race_keeps_its_quote_provenance(session):
    rows = _seed_final(session)
    session.execute(Race.__table__.update().where(Race.race_id == FINAL_RID)
                    .values(race_date=datetime.date(2025, 10, 5)))
    session.commit()
    before = _prov(session, FINAL_RID)

    out = apply_final_odds(session, FINAL_RID, rows, observed_at=FINAL_OBSERVED)
    session.commit()

    assert out.skipped_pre_netkeiba == 1 and out.updated == 0
    assert _prov(session, FINAL_RID) == before


def test_final_odds_naive_observed_at_is_refused(session):
    rows = _seed_final(session)
    with pytest.raises(ValueError, match="timezone-aware"):
        apply_final_odds(session, FINAL_RID, rows, observed_at=datetime.datetime(2026, 7, 19, 8))


def test_a_later_quote_cannot_overwrite_a_final_value_once_settled(session):
    """Once results are in, the quote endpoint is fill-if-null: the final odds and their
    provenance written by apply_final_odds survive a later quote payload."""
    rows = _seed_final(session)
    apply_final_odds(session, FINAL_RID, rows, observed_at=FINAL_OBSERVED)
    for number, row in enumerate(rows, start=1):
        session.add(RaceResult(race_id=FINAL_RID, horse_id=f"nk:{row.netkeiba_horse_id}",
                               finish_order=number, result_status=ResultStatus.FINISHED))
    session.commit()

    quote = {f"{n:02d}": ["1.5", "0.0", "1"] for n in range(1, 17)}
    c = update_odds(session, FINAL_RID, parse_odds(json.dumps(
        {"status": "result", "data": {"odds": {"1": quote}, "official_datetime": T2}}),
        FINAL_RID))
    session.commit()

    assert c.written == 0 and c.skipped == 16
    assert _prov(session, FINAL_RID) == (None, FINAL_OBSERVED)
