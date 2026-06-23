"""netkeiba ID -> JRA-VAN canonical_id via id_mappings ONLY — no guess-join (constitution I).

resolve_entity returns the JRA-VAN canonical_id when a 'mapped' row exists, otherwise a unique
``nk:{netkeiba_id}`` surrogate (and queues an UNMAPPED row). The surrogate is unique per netkeiba
id (no reused "Unknown" -> no cross-horse history leak) and the ``nk:`` prefix never collides with
JRA-VAN numeric IDs. Name/birth-year guessing is never used.
"""

from __future__ import annotations

from horseracing_db.enums import MappingStatus, Source
from horseracing_db.models import IdMapping
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import SURROGATE_PREFIX


def surrogate_id(netkeiba_id: str) -> str:
    return f"{SURROGATE_PREFIX}{netkeiba_id}"


def resolve_entity(session: Session, *, entity_type: str, netkeiba_id: str) -> str:
    """canonical_id if mapped, else a unique surrogate (queuing an UNMAPPED row)."""
    row = session.execute(
        select(IdMapping).where(
            IdMapping.entity_type == entity_type,
            IdMapping.source == Source.NETKEIBA,
            IdMapping.source_id == netkeiba_id,
        )
    ).scalar_one_or_none()

    if row is not None and row.mapping_status == MappingStatus.MAPPED and row.canonical_id:
        return row.canonical_id

    if row is None:
        # queue as UNMAPPED (idempotent; never overwrite an existing mapped row)
        stmt = insert(IdMapping).values(
            entity_type=entity_type, source=Source.NETKEIBA, source_id=netkeiba_id,
            canonical_id=None, mapping_status=MappingStatus.UNMAPPED,
        ).on_conflict_do_nothing(index_elements=["entity_type", "source", "source_id"])
        session.execute(stmt)

    return surrogate_id(netkeiba_id)
