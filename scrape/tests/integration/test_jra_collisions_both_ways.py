"""176 T055 (SC-011): collisions between the two routes, in both orders, with the real fixture.

A quarantined race keeps every column of every existing row; the (nk:, official) pair is reported
as a merge candidate, an approved mapping that was never re-keyed as pending re-key — neither is
written by this route (the re-key is spec 067's).
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import MappingStatus, Source
from horseracing_db.models import Horse, IdMapping, RaceHorse
from sqlalchemy import func, select

from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.upsert_jra import upsert_jra_entries
from horseracing_scrape.models import ResolvedEntryHorse
from horseracing_scrape.upsert import write_entries
from tests._seed176 import snapshot_rows
from tests.jra_fixtures import fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
RID = "202605041111"
OBS = fetched_at("entries_tokyo_20261101.sjis.html")
NK = Source.NETKEIBA


def _page(html=None):
    return parse_entries_day(html or jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)


def _changes():
    return parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)


def _entry(page=None):
    return next(b.payload for b in (page or _page()).published() if b.race_id == RID)


def _as_netkeiba(entry, *, rename=None):
    """The same field as the netkeiba route would write it (ids per ``rename``)."""
    rename = rename or {}
    return [
        ResolvedEntryHorse(
            horse_id=rename.get(h.horse_id, h.horse_id),
            horse_name=h.horse_name,
            frame=h.frame,
            horse_number=h.horse_number,
            jockey_id=h.jockey_id,
            jockey_name=h.jockey_name,
            trainer_id=h.trainer_id,
            trainer_name=h.trainer_name,
            weight=h.weight,
            weight_diff=h.weight_diff,
            jockey_weight=h.jockey_weight,
            sex=h.sex,
            age=h.age,
            entry_status=h.entry_status,
            birth_year=D.year - h.age if h.age else None,
            data_source=NK,
        )
        for h in entry.horses
    ]


def _values(entry):
    r = entry.race
    return dict(
        race_date=r.race_date,
        race_number=r.race_no,
        venue_code=r.venue_code,
        distance=r.distance,
        track_type=r.track_type,
        going=None,
        weather=None,
        race_class=r.race_class,
        race_name=r.race_name,
        grade=r.grade,
        post_time=r.post_time,
        prize_money=r.prize_money,
    )


def _official(session, page=None):
    c = upsert_jra_entries(session, page or _page(), _changes(), observed_at=OBS, race_ids=[RID])
    session.commit()
    return c


def _count(session):
    return session.scalar(
        select(func.count()).select_from(RaceHorse).where(RaceHorse.race_id == RID)
    )


def test_netkeiba_surrogate_first_then_official(session):
    e = _entry()
    first = e.horses[0].horse_id
    write_entries(
        session,
        RID,
        _values(e),
        _as_netkeiba(e, rename={first: f"nk:{first}"}),
        source=NK,
        observed_at=OBS,
        job_id=None,
        page_kind="entries",
    )
    session.commit()
    before = snapshot_rows(session, RID)
    c = _official(session)
    assert c.extra.get("quarantined_collision") == 1
    assert c.extra.get("merge_candidates") == 1
    assert any(f"nk:{first}" in m and first in m for m in c.error_messages)
    assert snapshot_rows(session, RID) == before and _count(session) == len(e.horses)


def test_official_first_then_netkeiba_unresolved_surrogate(session):
    e = _entry()
    _official(session)
    before = snapshot_rows(session, RID)
    first = e.horses[0].horse_id
    session.merge(Horse(horse_id=f"nk:{first}", horse_name=e.horses[0].horse_name, birth_year=2022))
    session.commit()
    c = write_entries(
        session,
        RID,
        _values(e),
        _as_netkeiba(e, rename={first: f"nk:{first}"}),
        source=NK,
        observed_at=OBS,
        job_id=None,
        page_kind="entries",
    )
    session.commit()
    assert c.extra.get("quarantined_collision") == 1
    assert snapshot_rows(session, RID) == before and _count(session) == len(e.horses)


def test_same_number_other_id_and_same_id_other_number(session):
    e = _entry()
    a, b = e.horses[0], e.horses[1]
    # stored: 馬番 1 belongs to a different canonical horse
    session.merge(Horse(horse_id="2019100001", horse_name="別の馬", birth_year=2019))
    session.commit()
    nk_rows = _as_netkeiba(e, rename={a.horse_id: "2019100001"})
    write_entries(
        session,
        RID,
        _values(e),
        nk_rows,
        source=NK,
        observed_at=OBS,
        job_id=None,
        page_kind="entries",
    )
    session.commit()
    before = snapshot_rows(session, RID)
    assert _official(session).extra.get("quarantined_collision") == 1
    assert snapshot_rows(session, RID) == before
    # stored: the two horses' numbers swapped (same ids, other numbers)
    session.execute(RaceHorse.__table__.delete().where(RaceHorse.race_id == RID))
    session.commit()
    swapped = [
        r
        if r.horse_id not in (a.horse_id, b.horse_id)
        else ResolvedEntryHorse(
            **{
                **r.__dict__,
                "horse_number": b.horse_number if r.horse_id == a.horse_id else a.horse_number,
            }
        )
        for r in _as_netkeiba(e)
    ]
    write_entries(
        session,
        RID,
        _values(e),
        swapped,
        source=NK,
        observed_at=OBS,
        job_id=None,
        page_kind="entries",
    )
    session.commit()
    before = snapshot_rows(session, RID)
    assert _official(session).extra.get("quarantined_collision") == 1
    assert snapshot_rows(session, RID) == before


def test_approved_mapping_is_pending_rekey_and_not_written(session):
    e = _entry()
    first = e.horses[0].horse_id
    write_entries(
        session,
        RID,
        _values(e),
        _as_netkeiba(e, rename={first: f"nk:{first}"}),
        source=NK,
        observed_at=OBS,
        job_id=None,
        page_kind="entries",
    )
    session.add(
        IdMapping(
            entity_type="horse",
            source="netkeiba",
            source_id=first,
            canonical_id=first,
            mapping_status=MappingStatus.MAPPED,
        )
    )
    session.commit()
    before = snapshot_rows(session, RID)
    c = _official(session)
    assert c.extra.get("quarantined_collision") == 1
    assert c.extra.get("approved_mapping_pending_rekey") == 1 and not c.extra.get(
        "merge_candidates"
    )
    assert snapshot_rows(session, RID) == before


def test_cancelled_number_unresolved_writes_nothing(session):
    c = upsert_jra_entries(session, _page(), (), observed_at=OBS, race_ids=["202605041104"])
    session.commit()
    assert c.extra.get("quarantined_cancel_unresolved") == 1
    assert session.scalar(select(func.count()).select_from(RaceHorse)) == 0
