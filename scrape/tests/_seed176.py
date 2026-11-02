"""Seed helpers for the Feature 176 integration tests (dual-source writers)."""

from __future__ import annotations

import datetime
from decimal import Decimal

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import (
    Horse,
    Jockey,
    Race,
    RaceFieldProvenance,
    RaceHorse,
    RaceHorseFieldProvenance,
    RaceResult,
    Trainer,
)
from sqlalchemy import select

from horseracing_scrape.models import (
    ResolvedEntryHorse,
    ScrapedOdds,
    ScrapedOddsRow,
    ScrapedRaceKey,
)

JST = datetime.timezone(datetime.timedelta(hours=9))
RID = "202605041105"  # 2026-11-01 東京 5R (netkeiba era)
RACE_DATE = datetime.date(2026, 11, 1)
KEY = ScrapedRaceKey(year=2026, track_code="05", kai=4, nichime=11, race_no=5)
OLD_RID = "202405010101"  # 2024 — JRA-VAN era, must never be written by the official route
T0 = datetime.datetime(2026, 11, 1, 8, 0, tzinfo=JST)
T1 = datetime.datetime(2026, 11, 1, 8, 22, tzinfo=JST)
T2 = datetime.datetime(2026, 11, 1, 8, 30, tzinfo=JST)


def canonical(i: int) -> str:
    return f"20241000{i:02d}"


def seed_race(
    session,
    *,
    rid: str = RID,
    race_date=RACE_DATE,
    n: int = 4,
    with_result: bool = False,
    horse_ids=None,
    win_odds: Decimal | None = None,
) -> list[str]:
    """A race with ``n`` started canonical horses (numbers 1..n), optional results."""
    session.merge(
        Race(
            race_id=rid,
            race_number=int(rid[-2:]),
            race_date=race_date,
            venue_code=rid[4:6],
            distance=1600,
            track_type="芝",
        )
    )
    ids = list(horse_ids) if horse_ids is not None else [canonical(i) for i in range(1, n + 1)]
    for i, hid in enumerate(ids, start=1):
        session.merge(Horse(horse_id=hid, horse_name=f"馬{i}", birth_year=2022, sex="牡"))
        session.merge(Jockey(jockey_id=f"0100{i}", jockey_name=f"騎手{i}"))
        session.merge(Trainer(trainer_id=f"0200{i}", trainer_name=f"調教師{i}"))
        session.merge(
            RaceHorse(
                race_id=rid,
                horse_id=hid,
                horse_number=i,
                frame=i,
                jockey_id=f"0100{i}",
                trainer_id=f"0200{i}",
                jockey_weight=Decimal("56.0"),
                sex="牡",
                age=4,
                entry_status=EntryStatus.STARTED,
                odds=win_odds,
            )
        )
        if with_result:
            session.merge(
                RaceResult(
                    race_id=rid, horse_id=hid, finish_order=i, result_status=ResultStatus.FINISHED
                )
            )
    session.commit()
    return ids


def entry(i: int, hid: str | None = None, **over) -> ResolvedEntryHorse:
    base = dict(
        horse_id=hid or canonical(i),
        horse_name=f"馬{i}",
        frame=i,
        horse_number=i,
        jockey_id=f"0100{i}",
        jockey_name=f"騎手{i}",
        trainer_id=f"0200{i}",
        trainer_name=f"調教師{i}",
        weight=None,
        weight_diff=None,
        jockey_weight=56.0,
        sex="牡",
        age=4,
        entry_status=EntryStatus.STARTED,
    )
    base.update(over)
    return ResolvedEntryHorse(**base)


def race_values(**over) -> dict:
    base = dict(
        race_date=RACE_DATE,
        race_number=5,
        venue_code="05",
        distance=1600,
        track_type="芝",
        going=None,
        weather=None,
        race_class="１勝",
        race_name="テスト",
        grade=None,
        post_time=None,
        prize_money=550,
    )
    base.update(over)
    return base


def odds_payload(
    values: dict[int, float],
    *,
    official_at,
    place: dict[int, tuple[float, float]] | None = None,
    popularity: dict[int, int] | None = None,
) -> ScrapedOdds:
    rows = tuple(ScrapedOddsRow(n, v, (popularity or {}).get(n)) for n, v in values.items())
    place_rows = ()
    if place:
        from horseracing_scrape.models import ScrapedPlaceQuoteRow

        place_rows = tuple(ScrapedPlaceQuoteRow(n, lo, hi, None) for n, (lo, hi) in place.items())
    return ScrapedOdds(key=KEY, rows=rows, place_rows=place_rows, official_at=official_at)


def horses(session, rid: str = RID) -> dict[str, RaceHorse]:
    session.expire_all()
    return {
        rh.horse_id: rh for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == rid))
    }


def horse_prov(session, rid: str = RID) -> dict[tuple[str, str], tuple[str, datetime.datetime]]:
    return {
        (r.horse_id, r.field_group): (r.source, r.observed_at)
        for r in session.scalars(
            select(RaceHorseFieldProvenance).where(RaceHorseFieldProvenance.race_id == rid)
        )
    }


def race_prov(
    session, rid: str = RID
) -> dict[str, tuple[str, datetime.datetime | None, datetime.datetime]]:
    return {
        r.field_group: (r.source, r.official_at, r.observed_at)
        for r in session.scalars(
            select(RaceFieldProvenance).where(RaceFieldProvenance.race_id == rid)
        )
    }


def snapshot_rows(session, rid: str = RID) -> dict[str, tuple]:
    """Every column of every race_horses row — for 'nothing changed' assertions."""
    out = {}
    for rh in horses(session, rid).values():
        out[rh.horse_id] = tuple(
            getattr(rh, c.name)
            for c in RaceHorse.__table__.columns
            if c.name not in ("updated_at",)
        )
    return out
