"""176 T056 (SC-010, entries part): the two routes re-ingesting the same races in every order,
with the official side coming from the real fixture pages."""

from __future__ import annotations

import dataclasses
import datetime

import pytest
from horseracing_db.enums import EntryStatus, Source
from horseracing_db.models import Race, RaceHorse
from sqlalchemy import func, select

from horseracing_scrape.jra.models import JraChange
from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.upsert_jra import apply_jra_changes, upsert_jra_entries
from horseracing_scrape.models import ResolvedEntryHorse
from horseracing_scrape.upsert import write_entries
from tests._seed176 import horse_prov, race_prov, snapshot_rows
from tests.jra_fixtures import JST, fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
OBS = fetched_at("entries_tokyo_20261101.sjis.html")
NK, JRA = Source.NETKEIBA, Source.JRA_OFFICIAL
R4, R10, R11 = "202605041104", "202605041110", "202605041111"


def _page():
    return parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)


def _changes():
    return parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)


def _entry(rid):
    return next(b.payload for b in _page().published() if b.race_id == rid)


def _official(session, rids, *, at=OBS):
    c = upsert_jra_entries(session, _page(), _changes(), observed_at=at, race_ids=rids)
    session.commit()
    return c


def _nk_rows(entry, **override):
    rows = []
    for h in entry.horses:
        if h.horse_number is None:
            continue  # netkeiba keeps the number; the official page blanks it for 取消
        r = ResolvedEntryHorse(
            horse_id=h.horse_id,
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
            birth_year=None,
            data_source=NK,
        )
        rows.append(dataclasses.replace(r, **override.get(h.horse_id, {})))
    return rows


def _values(entry, **over):
    r = entry.race
    v = dict(
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
    v.update(over)
    return v


def _netkeiba(session, rid, *, at, rows=None, values=None):
    e = _entry(rid)
    c = write_entries(
        session,
        rid,
        values or _values(e),
        rows or _nk_rows(e),
        source=NK,
        observed_at=at,
        job_id=None,
        page_kind="entries",
    )
    session.commit()
    return c


def _n(session, rid):
    return session.scalar(
        select(func.count()).select_from(RaceHorse).where(RaceHorse.race_id == rid)
    )


def test_netkeiba_official_netkeiba_converges_without_duplicates(session):
    _netkeiba(session, R11, at=OBS - datetime.timedelta(hours=10))
    _official(session, [R11])
    after_official = snapshot_rows(session, R11)
    _netkeiba(session, R11, at=OBS + datetime.timedelta(hours=1))
    assert _n(session, R11) == len(_entry(R11).horses)
    assert snapshot_rows(session, R11) == after_official  # same facts → nothing moves
    first = _entry(R11).horses[0].horse_id
    assert horse_prov(session, R11)[(first, "entry_static")][0] == JRA


def test_official_netkeiba_official_and_same_input_twice(session):
    _official(session, [R11])
    once = snapshot_rows(session, R11)
    _official(session, [R11])
    assert snapshot_rows(session, R11) == once
    _netkeiba(session, R11, at=OBS + datetime.timedelta(minutes=5))
    _official(session, [R11], at=OBS + datetime.timedelta(minutes=10))
    assert snapshot_rows(session, R11) == once and _n(session, R11) == len(_entry(R11).horses)


def test_netkeiba_with_a_different_static_value_is_kept_and_reported(session):
    _official(session, [R11])
    e = _entry(R11)
    first = e.horses[0]
    rows = _nk_rows(e, **{first.horse_id: {"jockey_weight": (first.jockey_weight or 55.0) + 1}})
    c = _netkeiba(session, R11, at=OBS + datetime.timedelta(hours=1), rows=rows)
    assert c.extra.get("static_mismatch", 0) >= 1
    stored = session.get(RaceHorse, (R11, first.horse_id))
    assert float(stored.jockey_weight) == first.jockey_weight  # the official value stays


def test_old_entries_page_cannot_undo_a_cancellation(session):
    _official(session, [R4])  # 4R #5 サルサベガ is 取消 (number recovered from the changes page)
    cancelled = next(h for h in _entry(R4).horses if h.entry_status == EntryStatus.CANCELLED)
    assert session.get(RaceHorse, (R4, cancelled.horse_id)).entry_status == EntryStatus.CANCELLED
    # an older netkeiba copy still shows the horse as started
    rows = _nk_rows(_entry(R4))
    rows.append(
        ResolvedEntryHorse(
            horse_id=cancelled.horse_id,
            horse_name=cancelled.horse_name,
            frame=cancelled.frame,
            horse_number=5,
            jockey_id=cancelled.jockey_id,
            jockey_name=cancelled.jockey_name,
            trainer_id=cancelled.trainer_id,
            trainer_name=cancelled.trainer_name,
            weight=None,
            weight_diff=None,
            jockey_weight=cancelled.jockey_weight,
            sex=cancelled.sex,
            age=cancelled.age,
            entry_status=EntryStatus.STARTED,
            birth_year=None,
            data_source=NK,
        )
    )
    _netkeiba(session, R4, at=OBS - datetime.timedelta(hours=12), rows=rows)
    assert session.get(RaceHorse, (R4, cancelled.horse_id)).entry_status == EntryStatus.CANCELLED


def test_jockey_change_is_not_rolled_back_by_an_older_entries_page(session):
    _official(session, [R11])
    e = _entry(R11)
    target, other = e.horses[0], e.horses[1]
    change = JraChange(
        R11,
        "jockey",
        target.horse_number,
        target.horse_name,
        None,
        None,
        other.jockey_name,
        target.jockey_name,
    )
    c = apply_jra_changes(session, [change], observed_at=OBS + datetime.timedelta(hours=1))
    session.commit()
    assert c.extra.get("jockey_changes") == 1
    assert session.get(RaceHorse, (R11, target.horse_id)).jockey_id == other.jockey_id
    # the morning page (older) and a netkeiba copy from before the change do not undo it
    _official(session, [R11], at=OBS)
    _netkeiba(session, R11, at=OBS - datetime.timedelta(hours=2))
    assert session.get(RaceHorse, (R11, target.horse_id)).jockey_id == other.jockey_id


def test_post_time_change_survives_a_later_netkeiba_entries_page(session):
    _official(session, [R10])  # also applies the changes page: 10R 15:00 → 15:01
    post = session.get(Race, R10).post_time
    assert post == datetime.datetime(2026, 11, 1, 15, 1, tzinfo=JST)
    e = _entry(R10)
    _netkeiba(
        session,
        R10,
        at=OBS + datetime.timedelta(hours=1),
        values=_values(e, post_time=datetime.datetime(2026, 11, 1, 15, 0, tzinfo=JST)),
    )
    session.expire_all()
    assert session.get(Race, R10).post_time == post
    assert race_prov(session, R10)["post_time"][0] == JRA
