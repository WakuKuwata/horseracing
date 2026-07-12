"""netkeiba ID -> JRA-VAN canonical_id via id_mappings (constitution I).

resolve_entity returns the JRA-VAN canonical_id when a 'mapped' row exists, otherwise a unique
``nk:{netkeiba_id}`` surrogate (and queues an UNMAPPED row). The surrogate is unique per netkeiba
id (no reused "Unknown" -> no cross-horse history leak) and the ``nk:`` prefix never collides with
JRA-VAN numeric IDs.

Feature 067: when the caller supplies identity evidence (name, and for horses birth_year) AND a
canonical master row already exists under the *same id* (netkeiba reuses JRA licence / 血統登録
numbers), ``classify_identity`` promotes the mapping to MAPPED at ingest time — so no new surrogate
split is created going forward. This is structural-id identity + corroboration, NOT a name/number
guess-join (constitution I). Evidence-absent paths (results = id only, pedigree parents = id only)
never auto-promote; CONFLICT/REJECTED rows are sticky.
"""

from __future__ import annotations

from horseracing_db.enums import EntityType, MappingStatus, Source
from horseracing_db.models import Horse, IdMapping, Jockey, Trainer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import SURROGATE_PREFIX
from .identity import classify_identity

_MASTER = {EntityType.HORSE: Horse, EntityType.JOCKEY: Jockey, EntityType.TRAINER: Trainer}


def surrogate_id(netkeiba_id: str) -> str:
    return f"{SURROGATE_PREFIX}{netkeiba_id}"


def _promote_mapped(session: Session, *, entity_type: str, netkeiba_id: str,
                    canonical_id: str, note: str) -> None:
    stmt = insert(IdMapping).values(
        entity_type=entity_type, source=Source.NETKEIBA, source_id=netkeiba_id,
        canonical_id=canonical_id, mapping_status=MappingStatus.MAPPED,
        resolution_note=note,
    ).on_conflict_do_update(
        index_elements=["entity_type", "source", "source_id"],
        set_={"canonical_id": canonical_id, "mapping_status": MappingStatus.MAPPED,
              "resolution_note": note},
    )
    session.execute(stmt)


def resolve_entity(
    session: Session,
    *,
    entity_type: str,
    netkeiba_id: str,
    candidate_name: str | None = None,
    candidate_birth_year: int | None = None,
) -> str:
    """canonical_id if mapped/identity-resolvable, else a unique surrogate (queuing UNMAPPED)."""
    row = session.execute(
        select(IdMapping).where(
            IdMapping.entity_type == entity_type,
            IdMapping.source == Source.NETKEIBA,
            IdMapping.source_id == netkeiba_id,
        )
    ).scalar_one_or_none()

    if row is not None:
        if row.mapping_status == MappingStatus.MAPPED and row.canonical_id:
            return row.canonical_id
        if row.mapping_status in (MappingStatus.CONFLICT, MappingStatus.REJECTED):
            return surrogate_id(netkeiba_id)  # sticky — never re-evaluate

    # identity attempt (ingest-time, Feature 067): only when the caller gave usable evidence and a
    # canonical master already exists under the same id.
    if candidate_name:
        master_model = _MASTER[entity_type]
        canonical_row = session.get(master_model, netkeiba_id)
        if canonical_row is not None:
            res = classify_identity(
                entity_type=entity_type, source_id=netkeiba_id, candidate_name=candidate_name,
                canonical_row=canonical_row, candidate_birth_year=candidate_birth_year,
            )
            if res.status == MappingStatus.MAPPED:
                _promote_mapped(session, entity_type=entity_type, netkeiba_id=netkeiba_id,
                                canonical_id=res.canonical_id, note=res.reason)
                return res.canonical_id

    if row is None:
        # queue as UNMAPPED (idempotent; never overwrite an existing mapped row)
        stmt = insert(IdMapping).values(
            entity_type=entity_type, source=Source.NETKEIBA, source_id=netkeiba_id,
            canonical_id=None, mapping_status=MappingStatus.UNMAPPED,
        ).on_conflict_do_nothing(index_elements=["entity_type", "source", "source_id"])
        session.execute(stmt)

    return surrogate_id(netkeiba_id)
