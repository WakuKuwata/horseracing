"""178 T043: emergency mode — the official 出馬表 creates rows only when the policy flag is on; a
later KYI keeps the official static values; through the production ``jra-entries`` entry as well."""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest
from horseracing_db.enums import Source
from horseracing_db.models import Horse, Race, RaceHorse
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from horseracing_scrape import precedence as P
from horseracing_scrape.jra.emergency_entries import create_entries_from_official, emergency_enabled
from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.models import ResolvedEntryHorse
from horseracing_scrape.upsert import write_entries
from tests.jra_fixtures import JST, fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
OBS = fetched_at("entries_tokyo_20261101.sjis.html")


def _page():
    return parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)


def _changes():
    return parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)


def test_flag_off_creates_nothing(session):
    c = create_entries_from_official(session, _page(), policy=False, observed_at=OBS)
    session.commit()
    assert c.written == 0 and c.extra == {}
    assert session.scalar(select(func.count()).select_from(RaceHorse)) == 0


def test_flag_on_creates_canonical_rows_with_official_provenance(session):
    page = _page()
    c = create_entries_from_official(session, page, _changes(), policy=True, observed_at=OBS)
    session.commit()
    n = sum(len(b.payload.horses) for b in page.published())
    assert (
        c.extra.get("emergency_rows_created") == n and c.extra.get("emergency_races_created") == 12
    )
    assert session.scalar(select(func.count()).select_from(RaceHorse)) == n
    assert (
        session.scalar(
            select(func.count()).select_from(RaceHorse).where(RaceHorse.horse_id.like("nk:%"))
        )
        == 0
    )
    h = session.get(Horse, "2024107186")
    assert h is not None and h.data_source == Source.JRA_OFFICIAL and h.birth_year == 2024
    race = session.get(Race, "202605041101")
    assert race.track_type in ("芝", "ダ", "障") and (
        race.race_class is None or "クラス" not in race.race_class
    )
    src = (
        session.execute(text("SELECT DISTINCT source FROM race_horse_field_provenance"))
        .scalars()
        .all()
    )
    assert src == [Source.JRA_OFFICIAL]
    # second call: the field exists → nothing created again
    c2 = create_entries_from_official(session, page, _changes(), policy=True, observed_at=OBS)
    assert (
        not c2.extra.get("emergency_rows_created")
        and c2.extra.get("emergency_skipped_existing_field") == 12
    )


def test_a_later_kyi_keeps_the_official_static_values(session):
    page = _page()
    create_entries_from_official(session, page, _changes(), policy=True, observed_at=OBS)
    session.commit()
    block = page.published()[0]
    entry = block.payload
    rid = entry.race.race_id
    h0 = entry.horses[0]
    before = session.get(RaceHorse, (rid, h0.horse_id)).frame
    jrdb_rows = [
        ResolvedEntryHorse(
            h.horse_id,
            h.horse_name,
            (h.frame or 1) % 8 + 1,
            h.horse_number,
            None,
            None,
            None,
            None,
            None,
            None,
            h.jockey_weight,
            h.sex,
            h.age,
            h.entry_status,
        )
        for h in entry.horses
    ]
    values = {
        "race_date": D,
        "race_number": entry.race.race_no,
        "venue_code": "05",
        "distance": 9999,
        "post_time": None,
    }
    c = write_entries(
        session,
        rid,
        values,
        jrdb_rows,
        source=P.JRDB,
        observed_at=OBS + datetime.timedelta(hours=20),
        job_id=None,
        page_kind=P.PAGE_ENTRIES,
    )
    session.commit()
    session.expire_all()
    assert session.get(RaceHorse, (rid, h0.horse_id)).frame == before  # 先着保持
    assert session.get(Race, rid).distance != 9999
    assert c.extra.get("static_mismatch", 0) >= 1


def test_through_the_production_jra_entries_entry(database_url, engine, tmp_path):
    from horseracing_scrape.jra.cli import run_command
    from tests._jra_fake import FakeJraFetcher

    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps({"jra_official_interval_seconds": 10, "jra_emergency_entries": True}),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        command="jra-entries",
        date=D.isoformat(),
        venue="05",
        race_ids=None,
        dry_run=False,
        json=True,
        database_url=database_url,
        policy=str(policy),
        archive_dir=None,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    result = run_command(
        "jra-entries",
        args,
        fetcher=FakeJraFetcher(now=datetime.datetime(2026, 11, 1, 8, 25, tzinfo=JST)),
        session_factory=factory,
        date=D,
    )
    assert result.exit_code == 0
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(RaceHorse)) > 0
        job = s.execute(
            text(
                "SELECT summary FROM ingestion_jobs WHERE job_type = 'jra_entries' ORDER BY created_at DESC LIMIT 1"
            )
        ).scalar_one()
    assert job.get("emergency_rows_created", 0) > 0
    # the flag is read from the policy file, nothing else
    assert emergency_enabled(policy) is True
    policy.write_text(json.dumps({"jra_official_interval_seconds": 10}), encoding="utf-8")
    assert emergency_enabled(policy) is False
