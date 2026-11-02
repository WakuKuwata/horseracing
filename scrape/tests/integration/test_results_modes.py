"""176 T025: the two result-insert modes (contracts/precedence.md §5, FR-014)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import ResultStatus, Source
from horseracing_db.models import RaceHorse, RaceResult
from sqlalchemy import select

from horseracing_scrape.models import ResolvedResultRow
from horseracing_scrape.upsert import PER_HORSE_LEGACY, RACE_ATOMIC, _insert_results
from tests._seed176 import RID, T1, canonical, race_prov, seed_race

pytestmark = pytest.mark.integration
JRA, NK = Source.JRA_OFFICIAL, Source.NETKEIBA


def _rows(n=3, corners=True):
    return [
        ResolvedResultRow(
            canonical(i),
            i,
            ResultStatus.FINISHED,
            f"1:3{i}.0",
            35.0 + i,
            ("3", "2") if corners else None,
        )
        for i in range(1, n + 1)
    ]


def _results(session):
    session.expire_all()
    return {
        r.horse_id: r for r in session.scalars(select(RaceResult).where(RaceResult.race_id == RID))
    }


def test_race_atomic_inserts_a_clean_race_and_records_provenance(session):
    seed_race(session, n=3)
    c = _insert_results(session, RID, _rows(), mode=RACE_ATOMIC, source=JRA, observed_at=T1)
    session.commit()
    assert c.written == 3 and c.extra.get("results_inserted_races") == 1
    assert len(_results(session)) == 3
    assert race_prov(session)["results"][0] == JRA
    # official route never fills running_style (that is the netkeiba legacy exception)
    styles = session.scalars(select(RaceHorse.running_style).where(RaceHorse.race_id == RID)).all()
    assert styles == [None, None, None]


def test_race_atomic_inserts_nothing_once_any_result_row_exists(session):
    seed_race(session, n=3)
    _insert_results(session, RID, _rows()[:1], mode=PER_HORSE_LEGACY, source=NK, observed_at=T1)
    session.commit()
    different = [
        ResolvedResultRow(canonical(1), 2, ResultStatus.FINISHED, "1:31.0", 36.0, None)
    ] + _rows()[1:]
    c = _insert_results(session, RID, different, mode=RACE_ATOMIC, source=JRA, observed_at=T1)
    session.commit()
    assert c.written == 0 and c.extra.get("results_existing_races") == 1
    assert c.extra.get("results_mismatch") == 1  # horse 1: stored 1st, official says 2nd
    assert len(_results(session)) == 1  # the other two horses were NOT added by the official route
    assert race_prov(session)["results"][0] == NK  # first arrival keeps the provenance


def test_race_atomic_one_bad_row_inserts_nothing(session):
    seed_race(session, n=3)
    rows = _rows()
    rows[2] = ResolvedResultRow(canonical(3), None, ResultStatus.FINISHED, "1:33.0", 38.0, None)
    c = _insert_results(session, RID, rows, mode=RACE_ATOMIC, source=JRA, observed_at=T1)
    session.commit()
    assert c.errors == 1 and c.written == 0 and _results(session) == {}
    assert "results" not in race_prov(session)


def test_per_horse_legacy_adds_and_fills_nulls(session):
    seed_race(session, n=3)
    _insert_results(
        session, RID, _rows(corners=False)[:2], mode=PER_HORSE_LEGACY, source=NK, observed_at=T1
    )
    session.commit()
    assert len(_results(session)) == 2
    c = _insert_results(session, RID, _rows(), mode=PER_HORSE_LEGACY, source=NK, observed_at=T1)
    session.commit()
    res = _results(session)
    assert len(res) == 3 and c.written == 3
    assert res[canonical(1)].corner_orders == [
        "3",
        "2",
    ]  # NULL corner fill (the enumerated exception)
    assert res[canonical(1)].finish_order == 1  # values inserted first are never overwritten
    styles = session.scalars(select(RaceHorse.running_style).where(RaceHorse.race_id == RID)).all()
    assert all(s is not None for s in styles)


def test_non_starters_never_get_a_result_row(session):
    seed_race(session, n=2)
    rows = _rows(2) + [ResolvedResultRow(canonical(9), None, "cancelled", None)]
    c = _insert_results(session, RID, rows, mode=RACE_ATOMIC, source=JRA, observed_at=T1)
    session.commit()
    assert c.written == 2 and c.skipped == 1 and len(_results(session)) == 2
