"""176 T054: the official entries page → the stored field (FR-011a: written onto the netkeiba
route's rows, never a new race / race_horses / master row; zero nk: rows created — SC-002)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, Source
from horseracing_db.models import Horse, IdMapping, Jockey, Race, RaceHorse, Trainer
from sqlalchemy import func, select

from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.upsert_jra import upsert_jra_entries
from tests._netkeiba_first import netkeiba_first, nk
from tests._seed176 import horse_prov, race_prov
from tests.jra_fixtures import JST, fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
JRA = Source.JRA_OFFICIAL
OBS = fetched_at("entries_tokyo_20261101.sjis.html")
EARLY = OBS - datetime.timedelta(hours=10)


def _page():
    return parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)


def _changes():
    return parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)


def _counts(session) -> dict:
    return {
        m.__name__: session.scalar(select(func.count()).select_from(m))
        for m in (Race, RaceHorse, Horse, Jockey, Trainer, IdMapping)
    }


def test_full_day_ingest_writes_onto_the_netkeiba_rows_only(session):
    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLY)
    before = _counts(session)
    c = upsert_jra_entries(session, page, changes, observed_at=OBS)
    session.commit()
    assert c.errors == 0 and c.extra.get("quarantined_collision", 0) == 0
    n_horses = sum(len(b.payload.horses) for b in page.races)
    assert c.written == n_horses and c.extra.get("alias_horses") == n_horses
    # no row of any kind is created by the official route, in particular no canonical master
    assert _counts(session) == before
    assert session.get(Horse, "2024107186") is None
    assert session.get(Jockey, "05339") is None and session.get(Trainer, "01165") is None
    assert session.scalar(select(func.count()).select_from(Race).where(Race.race_date == D)) == 12
    # the nk: rows carry the official values; the persons stay on the stored (nk:) ids
    rh = session.get(RaceHorse, ("202605041101", nk("2024107186")))
    assert rh.jockey_id is not None and rh.jockey_id.startswith("nk:")
    race = session.get(Race, "202605041101")
    assert race.post_time == datetime.datetime(2026, 11, 1, 10, 0, tzinfo=JST)
    assert (race.distance, race.track_type, race.prize_money, race.race_class) == (
        1400,
        "芝",
        590,
        "未勝利",
    )
    # cancelled horses keep their 馬番, status cancelled
    salsa = session.get(RaceHorse, ("202605041104", nk("2024102799")))
    assert salsa.entry_status == EntryStatus.CANCELLED and salsa.horse_number == 5
    # provenance on the stored (nk:) id, source official
    pv = horse_prov(session, "202605041101")
    key = nk("2024107186")
    assert pv[(key, "entry_static")][0] == JRA and pv[(key, "entry_static")][1] == OBS
    assert race_prov(session, "202605041101")["post_time"][0] == JRA


def test_canonical_field_is_written_without_new_rows(session):
    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLY, alias=False)
    before = _counts(session)
    c = upsert_jra_entries(session, page, changes, observed_at=OBS)
    session.commit()
    n_horses = sum(len(b.payload.horses) for b in page.races)
    assert c.written == n_horses and not c.extra.get("alias_horses")
    assert _counts(session) == before
    assert horse_prov(session, "202605041101")[("2024107186", "entry_static")][0] == JRA


def test_official_before_netkeiba_waits_and_writes_nothing(session):
    c = upsert_jra_entries(session, _page(), _changes(), observed_at=OBS)
    session.commit()
    assert c.extra.get("awaiting_netkeiba_entries") == 12 and c.written == 0
    assert not c.extra.get("quarantined_collision")
    assert all(v == 0 for v in _counts(session).values())


def test_reingest_is_idempotent_and_master_mismatch_is_reported_not_written(session):
    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLY, alias=False)
    upsert_jra_entries(session, page, changes, observed_at=OBS)
    session.commit()
    # someone renames a master row: the official page disagrees → report, never overwrite
    session.execute(
        Jockey.__table__.update().where(Jockey.jockey_id == "05339").values(jockey_name="別名")
    )
    session.commit()
    before = _counts(session)
    c = upsert_jra_entries(session, page, changes, observed_at=OBS)
    session.commit()
    assert _counts(session) == before
    assert c.extra.get("master_name_mismatch", 0) >= 1
    assert any("05339" in m for m in c.error_messages)
    assert session.get(Jockey, "05339").jockey_name == "別名"  # master untouched
    assert (
        session.scalar(select(func.count()).select_from(Horse).where(Horse.horse_id.like("nk:%")))
        == 0
    )


def test_race_id_filter_writes_only_that_block(session):
    page, changes = _page(), _changes()
    netkeiba_first(session, page, changes, observed_at=EARLY)
    c = upsert_jra_entries(session, page, changes, observed_at=OBS, race_ids=["202605041111"])
    session.commit()
    assert c.written == len(
        next(b for b in page.races if b.race_id == "202605041111").payload.horses
    )
    official = {
        rid
        for rid in (f"2026050411{i:02d}" for i in range(1, 13))
        if any(src == JRA for src, _ in horse_prov(session, rid).values())
    }
    assert official == {"202605041111"}


def test_pre_netkeiba_era_race_is_never_written(session):
    page = parse_entries_day(
        jra_text("entries_tokyo_20261101.sjis.html").replace(
            "2026年11月1日（日曜） 4回東京11日", "2024年11月1日（金曜） 4回東京11日"
        ),
        expected_date=datetime.date(2024, 11, 1),
    )
    c = upsert_jra_entries(
        session, page, (), observed_at=fetched_at("entries_tokyo_20261101.sjis.html")
    )
    session.commit()
    assert c.extra.get("skipped_pre_netkeiba_era") == 12 and c.written == 0
    assert session.scalar(select(func.count()).select_from(Race)) == 0


def test_unknown_id_quarantines_the_race(session):
    netkeiba_first(session, _page(), _changes(), observed_at=EARLY)
    html = jra_text("entries_tokyo_20261101.sjis.html").replace(
        "pw01dud002024107186/DA", "pw01dud00202410718/DA", 1
    )
    page = parse_entries_day(html, expected_date=D)
    c = upsert_jra_entries(session, page, _changes(), observed_at=OBS)
    session.commit()
    assert c.extra.get("quarantined_unknown_id") == 1
    assert not any(src == JRA for src, _ in horse_prov(session, "202605041101").values())
    assert any(src == JRA for src, _ in horse_prov(session, "202605041102").values())


def test_cancelled_number_unresolved_quarantines_the_race(session):
    page = _page()
    cancelled = [
        h.horse_id for b in page.published() for h in b.payload.horses if h.horse_number is None
    ]
    assert len(cancelled) == 2  # 4R and 6R each have a 取消
    # the stored rows of the two 取消 have no 馬番 either → nothing to recover it from
    netkeiba_first(session, page, _changes(), observed_at=EARLY, blank_numbers=cancelled)
    c = upsert_jra_entries(session, page, (), observed_at=OBS)
    session.commit()
    assert c.extra.get("quarantined_cancel_unresolved") == 2
    assert c.written == sum(
        len(b.payload.horses)
        for b in page.published()
        if b.race_id not in ("202605041104", "202605041106")
    )
