"""178 T045: ``write_id_mappings`` — mapped/conflict/unmapped rows with reasons, existing mapped
rows untouched, dry-run writes nothing."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import IdMapping
from sqlalchemy import func, select

from horseracing_scrape.jra.person_profile import JraPersonProfile
from horseracing_scrape.jrdb import persons
from horseracing_scrape.jrdb.models import JrdbPerson

pytestmark = pytest.mark.integration


def _p(code, name, birth, kind="jockey"):
    return JrdbPerson(kind, code, False, None, name, None, None, 1, None, birth, 2000, None)


def _report():
    master = [
        _p("10076", "岡部幸雄", datetime.date(1948, 10, 31)),
        _p("10077", "横山典弘", datetime.date(1968, 2, 23)),
        _p("10078", "誰か", datetime.date(1970, 1, 1)),
        _p("10379", "中尾秀正", datetime.date(1950, 3, 3), kind="trainer"),
    ]
    profiles = [
        JraPersonProfile("jockey", "00140", "岡部 幸雄", datetime.date(1948, 10, 31), "美浦"),
        JraPersonProfile(
            "jockey", "00141", "横山典弘", datetime.date(1960, 1, 1), "美浦"
        ),  # birth differs → conflict
        JraPersonProfile("trainer", "00300", "中尾秀正", datetime.date(1950, 3, 3), "栗東"),
    ]
    codes = persons.codes_from_rows(
        [
            ("jockey", "10076", 2026),
            ("jockey", "10077", 2026),
            ("jockey", "10078", 2026),
            ("trainer", "10379", 2026),
        ]
    )
    return persons.build_report(master, profiles, codes_in_data=codes)


def test_dry_run_writes_nothing(session):
    out = persons.write_id_mappings(session, _report(), dry_run=True)
    session.commit()
    assert out == {
        "mapped": 2,
        "conflict": 1,
        "unmapped": 1,
        "kept_existing_mapped": 0,
        "written": 0,
    }
    assert session.scalar(select(func.count()).select_from(IdMapping)) == 0


def test_rows_are_written_with_status_and_reason_and_existing_mapped_is_kept(session):
    # an operator-approved mapping already exists for 10077 → never overwritten by a conflict
    session.add(
        IdMapping(
            entity_type="jockey",
            source="jrdb",
            source_id="10077",
            canonical_id="00999",
            mapping_status="mapped",
            resolution_note="manual",
        )
    )
    session.commit()
    out = persons.write_id_mappings(session, _report(), dry_run=False)
    session.commit()
    assert out["written"] == 3 and out["kept_existing_mapped"] == 1
    rows = {
        (r.entity_type, r.source_id): r
        for r in session.scalars(select(IdMapping).where(IdMapping.source == "jrdb"))
    }
    assert (
        rows[("jockey", "10076")].mapping_status == "mapped"
        and rows[("jockey", "10076")].canonical_id == "00140"
    )
    assert (
        rows[("trainer", "10379")].mapping_status == "mapped"
        and rows[("trainer", "10379")].canonical_id == "00300"
    )
    assert (
        rows[("jockey", "10078")].mapping_status == "unmapped"
        and rows[("jockey", "10078")].canonical_id is None
    )
    assert (
        rows[("jockey", "10077")].mapping_status == "mapped"
        and rows[("jockey", "10077")].canonical_id == "00999"
    )
    assert "name+birth_date" in rows[("jockey", "10076")].resolution_note
    assert "178 persons" in rows[("jockey", "10078")].resolution_note
    # a second run is idempotent
    out2 = persons.write_id_mappings(session, _report(), dry_run=False)
    session.commit()
    # the three mapped rows are kept; only the unmapped row is rewritten (same content)
    assert out2["written"] == 1 and out2["kept_existing_mapped"] == 3
    assert session.scalar(select(func.count()).select_from(IdMapping)) == 4


def test_conflict_reason_is_recorded(session):
    rep = _report()
    # drop the manual row scenario: write fresh, the 10077 conflict lands with its reason
    persons.write_id_mappings(session, rep, dry_run=False)
    session.commit()
    row = session.scalar(
        select(IdMapping).where(IdMapping.source == "jrdb", IdMapping.source_id == "10077")
    )
    assert (
        row.mapping_status == "conflict"
        and "birth date differs" in row.resolution_note
        and row.canonical_id is None
    )
