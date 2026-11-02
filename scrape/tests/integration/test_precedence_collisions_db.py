"""176 T022/T055: nk:/canonical collisions quarantine the race in BOTH directions (SC-011)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import Source
from horseracing_db.models import Horse, IdMapping, Race, RaceHorse
from sqlalchemy import func, select

from horseracing_scrape.upsert import write_entries
from tests._seed176 import RID, T1, canonical, entry, race_values, seed_race, snapshot_rows

pytestmark = pytest.mark.integration
JRA, NK = Source.JRA_OFFICIAL, Source.NETKEIBA


def _count_rows(session):
    return session.scalar(
        select(func.count()).select_from(RaceHorse).where(RaceHorse.race_id == RID)
    )


def test_netkeiba_surrogate_after_official_canonical_is_quarantined(session):
    seed_race(session, n=3)  # canonical 1..3 (the official route wrote them)
    before = snapshot_rows(session)
    # netkeiba resolves horse 2 to an unmapped surrogate → same number, different id
    rows = [entry(1), entry(2, hid="nk:2024100002", data_source="netkeiba"), entry(3)]
    c = write_entries(session, RID, race_values(), rows, source=NK, observed_at=T1)
    session.commit()
    assert c.extra.get("quarantined_collision") == 1 and c.skipped == 3 and c.written == 0
    assert _count_rows(session) == 3  # no second row for 馬番 2
    assert snapshot_rows(session) == before  # not one existing row changed
    assert session.get(Horse, "nk:2024100002") is None  # no master row either


def test_official_canonical_after_netkeiba_surrogate_is_quarantined_with_candidates(session):
    seed_race(session, n=3, horse_ids=[canonical(1), "nk:2024100002", canonical(3)])
    session.merge(Horse(horse_id="nk:2024100002", horse_name="馬2", birth_year=2022))
    session.commit()
    before = snapshot_rows(session)
    rows = [entry(1), entry(2, birth_year=2022, data_source="jra_official"), entry(3)]
    c = write_entries(session, RID, race_values(), rows, source=JRA, observed_at=T1)
    session.commit()
    assert c.extra.get("quarantined_collision") == 1
    assert c.extra.get("merge_candidates") == 1  # (nk:2024100002, 2024100002): same name + birth
    assert c.extra.get("approved_mapping_pending_rekey", 0) == 0
    assert "nk:2024100002" in c.error_messages[0] and canonical(2) in c.error_messages[0]
    assert snapshot_rows(session) == before
    assert session.get(Horse, canonical(2)) is None


def test_approved_mapping_is_reported_separately_and_still_not_written(session):
    seed_race(session, n=2, horse_ids=["nk:2024100001", canonical(2)])
    session.merge(Horse(horse_id="nk:2024100001", horse_name="馬1", birth_year=2022))
    session.add(
        IdMapping(
            entity_type="horse",
            source=NK,
            source_id="2024100001",
            canonical_id=canonical(1),
            mapping_status="mapped",
        )
    )
    session.commit()
    c = write_entries(session, RID, race_values(), [entry(1), entry(2)], source=JRA, observed_at=T1)
    session.commit()
    assert c.extra.get("quarantined_collision") == 1
    assert c.extra.get("approved_mapping_pending_rekey") == 1
    assert c.extra.get("merge_candidates", 0) == 0
    assert _count_rows(session) == 2


def test_quarantine_rolls_back_a_race_row_this_call_created(session):
    assert session.get(Race, RID) is None
    rows = [entry(1), entry(2, horse_number=1)]  # input duplicate number
    c = write_entries(session, RID, race_values(), rows, source=JRA, observed_at=T1)
    session.commit()
    assert c.extra.get("quarantined_collision") == 1
    session.expire_all()
    assert session.get(Race, RID) is None  # the savepoint took the races row with it


def test_surplus_started_row_is_a_collision(session):
    seed_race(session, n=3)
    c = write_entries(session, RID, race_values(), [entry(1), entry(2)], source=JRA, observed_at=T1)
    session.commit()
    assert c.extra.get("quarantined_collision") == 1
    assert "surplus" in c.error_messages[0]


def test_clean_reingest_is_not_quarantined(session):
    seed_race(session, n=3)
    c = write_entries(
        session, RID, race_values(), [entry(1), entry(2), entry(3)], source=JRA, observed_at=T1
    )
    session.commit()
    assert c.extra.get("quarantined_collision", 0) == 0 and c.written == 3
