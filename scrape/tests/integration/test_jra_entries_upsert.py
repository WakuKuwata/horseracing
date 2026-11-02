"""176 T054: the official entries page → races / race_horses / masters, with zero nk: rows (SC-002)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, Source
from horseracing_db.models import Horse, IdMapping, Jockey, Race, RaceHorse, Trainer
from sqlalchemy import func, select

from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.upsert_jra import upsert_jra_entries
from tests._seed176 import horse_prov, race_prov
from tests.jra_fixtures import JST, fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
JRA = Source.JRA_OFFICIAL


def _page():
    return parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)


def _changes():
    return parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)


def test_full_day_ingest_creates_canonical_rows_only(session):
    page, changes = _page(), _changes()
    obs = fetched_at("entries_tokyo_20261101.sjis.html")
    c = upsert_jra_entries(session, page, changes, observed_at=obs)
    session.commit()
    assert c.errors == 0 and c.extra.get("quarantined_collision", 0) == 0
    n_horses = sum(len(b.payload.horses) for b in page.races)
    assert c.written == n_horses
    assert session.scalar(select(func.count()).select_from(Race).where(Race.race_date == D)) == 12
    assert session.scalar(select(func.count()).select_from(RaceHorse)) == n_horses
    assert (
        session.scalar(select(func.count()).select_from(Horse).where(Horse.horse_id.like("nk:%")))
        == 0
    )
    assert session.scalar(select(func.count()).select_from(IdMapping)) == 0
    # masters carry the official ids as canonical ids; new horses get birth_year + data_source
    h = session.get(Horse, "2024107186")
    assert (
        h.horse_name == "インサイドエッジ"
        and h.birth_year == 2024
        and h.data_source == JRA
        and h.sex == "牝"
    )
    assert session.get(Jockey, "05339").jockey_name == "C.ルメール"
    assert session.get(Trainer, "01165").trainer_name == "和田 勇介"
    race = session.get(Race, "202605041101")
    assert race.post_time == datetime.datetime(2026, 11, 1, 10, 0, tzinfo=JST)
    assert (race.distance, race.track_type, race.prize_money, race.race_class) == (
        1400,
        "芝",
        590,
        "未勝利",
    )
    # cancelled horses keep their 馬番 (recovered from the changes page), status cancelled
    r4 = {
        rh.horse_name if False else rh.horse_id: rh
        for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == "202605041104"))
    }
    salsa = r4["2024102799"]
    assert salsa.entry_status == EntryStatus.CANCELLED and salsa.horse_number == 5
    # provenance per horse and per race, all official
    pv = horse_prov(session, "202605041101")
    assert (
        pv[("2024107186", "entry_static")][0] == JRA
        and pv[("2024107186", "entry_status")][0] == JRA
    )
    assert pv[("2024107186", "entry_static")][1] == obs
    assert race_prov(session, "202605041101")["race_static"][0] == JRA
    assert race_prov(session, "202605041101")["post_time"][0] == JRA


def test_reingest_is_idempotent_and_master_mismatch_is_reported_not_written(session):
    page, changes = _page(), _changes()
    upsert_jra_entries(
        session, page, changes, observed_at=fetched_at("entries_tokyo_20261101.sjis.html")
    )
    session.commit()
    # someone renames a master row: the official page disagrees → report, never overwrite
    session.execute(
        Jockey.__table__.update().where(Jockey.jockey_id == "05339").values(jockey_name="別名")
    )
    session.commit()
    before = session.scalar(select(func.count()).select_from(RaceHorse))
    c = upsert_jra_entries(
        session, page, changes, observed_at=fetched_at("entries_tokyo_20261101.sjis.html")
    )
    session.commit()
    assert session.scalar(select(func.count()).select_from(RaceHorse)) == before
    assert c.extra.get("master_name_mismatch", 0) >= 1
    assert any("05339" in m for m in c.error_messages)
    assert session.get(Jockey, "05339").jockey_name == "別名"  # master untouched
    assert (
        session.scalar(select(func.count()).select_from(Horse).where(Horse.horse_id.like("nk:%")))
        == 0
    )


def test_race_id_filter_writes_only_that_block(session):
    page, changes = _page(), _changes()
    c = upsert_jra_entries(
        session,
        page,
        changes,
        observed_at=fetched_at("entries_tokyo_20261101.sjis.html"),
        race_ids=["202605041111"],
    )
    session.commit()
    assert c.written == len(
        next(b for b in page.races if b.race_id == "202605041111").payload.horses
    )
    assert session.scalar(select(func.count()).select_from(Race)) == 1


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
    html = jra_text("entries_tokyo_20261101.sjis.html").replace(
        "pw01dud002024107186/DA", "pw01dud00202410718/DA", 1
    )
    page = parse_entries_day(html, expected_date=D)
    c = upsert_jra_entries(
        session, page, _changes(), observed_at=fetched_at("entries_tokyo_20261101.sjis.html")
    )
    session.commit()
    assert c.extra.get("quarantined_unknown_id") == 1
    assert (
        session.scalar(select(func.count()).select_from(Race).where(Race.race_id == "202605041101"))
        == 0
    )
    assert session.scalar(select(func.count()).select_from(Race)) == 11


def test_cancelled_number_unresolved_quarantines_the_race(session):
    page = _page()
    c = upsert_jra_entries(
        session, page, (), observed_at=fetched_at("entries_tokyo_20261101.sjis.html")
    )
    session.commit()
    assert c.extra.get("quarantined_cancel_unresolved") == 2  # 4R and 6R each have a 取消
    assert session.scalar(select(func.count()).select_from(Race)) == 10
