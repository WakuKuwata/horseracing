"""US3 / FR-013..015: id_mappings unmapped, uniqueness, conflict, CHECKs."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from horseracing_db.enums import EntityType, MappingStatus, Source
from horseracing_db.models import IdMapping

pytestmark = pytest.mark.integration


def test_unmapped_record_defaults(session):
    m = IdMapping(entity_type=EntityType.HORSE, source=Source.NETKEIBA, source_id="nk-123")
    session.add(m)
    session.flush()
    session.refresh(m)
    assert m.mapping_status == MappingStatus.UNMAPPED
    assert m.canonical_id is None


def test_unique_entity_source_sourceid(session):
    session.add(IdMapping(entity_type=EntityType.HORSE, source=Source.NETKEIBA, source_id="dup"))
    session.flush()
    session.add(IdMapping(entity_type=EntityType.HORSE, source=Source.NETKEIBA, source_id="dup"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_conflict_representation(session):
    group = uuid.uuid4()
    session.add_all([
        IdMapping(
            entity_type=EntityType.JOCKEY, source=Source.JRA_VAN, source_id="j-1",
            canonical_id="C-A", mapping_status=MappingStatus.CONFLICT, conflict_group_id=group,
        ),
        IdMapping(
            entity_type=EntityType.JOCKEY, source=Source.NETKEIBA, source_id="j-9",
            canonical_id="C-B", mapping_status=MappingStatus.CONFLICT, conflict_group_id=group,
        ),
    ])
    session.flush()
    rows = session.execute(
        select(IdMapping).where(IdMapping.conflict_group_id == group)
    ).scalars().all()
    assert len(rows) == 2
    assert all(r.mapping_status == MappingStatus.CONFLICT for r in rows)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"entity_type": "horse", "source": "bad_source", "source_id": "x"},
        {"entity_type": "bad_entity", "source": "netkeiba", "source_id": "x"},
    ],
)
def test_invalid_enum_rejected(session, kwargs):
    session.add(IdMapping(**kwargs))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_invalid_status_rejected(session):
    session.add(IdMapping(entity_type=EntityType.HORSE, source=Source.NETKEIBA,
                          source_id="x", mapping_status="weird"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
