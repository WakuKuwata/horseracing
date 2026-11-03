"""176 T055 (SC-011) + FR-011a: the two routes in both orders, with the real fixture.

FR-011a (2026-11-03): an official ``X`` whose stored row is ``nk:X`` (no canonical master, no
decided mapping, same name) is written THROUGH that row — no re-key, no new master. Everything
else that disagrees with the stored field quarantines the race and keeps every column of every
existing row: both ids present, an approved mapping that was never re-keyed (pending re-key — the
re-key is spec 067's), the same 馬番 holding another horse, two horses' numbers swapped.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import MappingStatus, Source
from horseracing_db.models import Horse, IdMapping, Jockey, Race, RaceHorse, Trainer
from sqlalchemy import func, select

from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.upsert_jra import upsert_jra_entries
from horseracing_scrape.models import ResolvedEntryHorse
from horseracing_scrape.upsert import write_entries
from tests._netkeiba_first import netkeiba_rows, nk
from tests._seed176 import horse_prov, snapshot_rows
from tests.jra_fixtures import fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
RID = "202605041111"
OBS = fetched_at("entries_tokyo_20261101.sjis.html")
EARLY = OBS - datetime.timedelta(hours=10)
NK = Source.NETKEIBA
JRA = Source.JRA_OFFICIAL


def _page(html=None):
    return parse_entries_day(html or jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)


def _changes():
    return parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)


def _entry(page=None):
    return next(b.payload for b in (page or _page()).published() if b.race_id == RID)


def _as_netkeiba(entry, *, rename=None):
    """The same field as the netkeiba route would write it, canonical ids except ``rename``."""
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


def _netkeiba(session, rows, *, at=EARLY):
    c = write_entries(
        session,
        RID,
        _values(_entry()),
        rows,
        source=NK,
        observed_at=at,
        job_id=None,
        page_kind="entries",
    )
    session.commit()
    return c


def _official(session, page=None):
    c = upsert_jra_entries(session, page or _page(), _changes(), observed_at=OBS, race_ids=[RID])
    session.commit()
    return c


def _count(session, model=RaceHorse):
    q = select(func.count()).select_from(model)
    if model is RaceHorse:
        q = q.where(RaceHorse.race_id == RID)
    return session.scalar(q)


def _masters(session):
    return tuple(_count(session, m) for m in (Horse, Jockey, Trainer, Race, IdMapping))


def test_netkeiba_surrogate_first_then_official_writes_through_the_alias(session):
    e = _entry()
    _netkeiba(session, netkeiba_rows(e, _changes()))  # the 2026 state: everything nk:
    before = _masters(session)
    c = _official(session)
    assert not c.extra.get("quarantined_collision") and c.written == len(e.horses)
    assert c.extra.get("alias_horses") == len(e.horses)
    assert _masters(session) == before and _count(session) == len(e.horses)
    first = e.horses[0]
    assert session.get(Horse, first.horse_id) is None  # no canonical master created
    rh = session.get(RaceHorse, (RID, nk(first.horse_id)))
    assert rh.horse_number == first.horse_number and rh.jockey_id == nk(first.jockey_id)
    assert horse_prov(session, RID)[(nk(first.horse_id), "entry_static")][0] == JRA


def test_one_surrogate_in_a_canonical_field_is_aliased(session):
    e = _entry()
    first = e.horses[0].horse_id
    _netkeiba(session, _as_netkeiba(e, rename={first: nk(first)}))
    before = snapshot_rows(session, RID)
    c = _official(session)
    assert not c.extra.get("quarantined_collision") and c.extra.get("alias_horses") == 1
    assert set(snapshot_rows(session, RID)) == set(before)  # same ids, no new row


def test_official_before_netkeiba_waits_and_netkeiba_is_unaffected(session):
    e = _entry()
    c = _official(session)
    assert c.extra.get("awaiting_netkeiba_entries") == 1 and c.written == 0
    assert _count(session) == 0 and _count(session, Race) == 0 and _count(session, Horse) == 0
    # netkeiba then writes its field as usual — nothing official to collide with
    nc = _netkeiba(session, netkeiba_rows(e, _changes()))
    assert not nc.extra.get("quarantined_collision") and _count(session) == len(e.horses)


def test_both_ids_exist_quarantines(session):
    e = _entry()
    first = e.horses[0]
    _netkeiba(session, _as_netkeiba(e, rename={first.horse_id: nk(first.horse_id)}))
    # a canonical master of the same number exists too (e.g. re-key half done)
    session.merge(Horse(horse_id=first.horse_id, horse_name=first.horse_name, birth_year=2024))
    session.commit()
    before = snapshot_rows(session, RID)
    c = _official(session)
    assert c.extra.get("alias_both_ids_exist") == 1 and c.extra.get("quarantined_collision") == 1
    assert snapshot_rows(session, RID) == before


def test_name_mismatch_quarantines(session):
    e = _entry()
    first = e.horses[0]
    _netkeiba(session, _as_netkeiba(e, rename={first.horse_id: nk(first.horse_id)}))
    session.execute(
        Horse.__table__.update()
        .where(Horse.horse_id == nk(first.horse_id))
        .values(horse_name="別の馬")
    )
    session.commit()
    before = snapshot_rows(session, RID)
    c = _official(session)
    assert c.extra.get("alias_name_mismatch") == 1 and c.extra.get("quarantined_collision") == 1
    assert snapshot_rows(session, RID) == before


def test_same_number_other_id_and_same_id_other_number(session):
    e = _entry()
    a, b = e.horses[0], e.horses[1]
    # stored: 馬番 1 belongs to a different canonical horse
    session.merge(Horse(horse_id="2019100001", horse_name="別の馬", birth_year=2019))
    session.commit()
    _netkeiba(session, _as_netkeiba(e, rename={a.horse_id: "2019100001"}))
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
    _netkeiba(session, swapped)
    before = snapshot_rows(session, RID)
    assert _official(session).extra.get("quarantined_collision") == 1
    assert snapshot_rows(session, RID) == before


def test_approved_mapping_is_pending_rekey_and_not_written(session):
    e = _entry()
    first = e.horses[0].horse_id
    _netkeiba(session, _as_netkeiba(e, rename={first: nk(first)}))
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
    assert c.extra.get("approved_mapping_pending_rekey") == 1
    assert snapshot_rows(session, RID) == before


def test_cancelled_number_unresolved_writes_nothing(session):
    page = _page()
    rid4 = "202605041104"
    e4 = next(b.payload for b in page.published() if b.race_id == rid4)
    cancelled = [h.horse_id for h in e4.horses if h.horse_number is None]
    write_entries(
        session,
        rid4,
        _values(e4),
        netkeiba_rows(e4, _changes(), blank_numbers=cancelled),
        source=NK,
        observed_at=EARLY,
        job_id=None,
        page_kind="entries",
    )
    session.commit()
    before = snapshot_rows(session, rid4)
    c = upsert_jra_entries(session, page, (), observed_at=OBS, race_ids=[rid4])
    session.commit()
    assert c.extra.get("quarantined_cancel_unresolved") == 1
    assert snapshot_rows(session, rid4) == before
