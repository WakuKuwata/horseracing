"""Idempotent FK-ordered upsert into the core tables (research R7)."""

from __future__ import annotations

from horseracing_db.models import (
    Horse,
    Jockey,
    Race,
    RaceHorse,
    RaceResult,
    Trainer,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .mapping import CoreRecords


def _upsert(session: Session, model, values: dict, pk: tuple[str, ...]) -> None:
    stmt = insert(model).values(**values)
    update_cols = {c: getattr(stmt.excluded, c) for c in values if c not in pk}
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=list(pk), set_=update_cols)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(pk))
    session.execute(stmt)


def upsert_core(session: Session, rec: CoreRecords) -> None:
    """Upsert one row's worth of records in FK-safe order."""
    _upsert(session, Race, rec.race, ("race_id",))
    _upsert(session, Horse, rec.horse, ("horse_id",))
    if rec.jockey:
        _upsert(session, Jockey, rec.jockey, ("jockey_id",))
    if rec.trainer:
        _upsert(session, Trainer, rec.trainer, ("trainer_id",))
    _upsert(session, RaceHorse, rec.race_horse, ("race_id", "horse_id"))
    if rec.race_result is not None:
        _upsert(session, RaceResult, rec.race_result, ("race_id", "horse_id"))
