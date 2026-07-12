"""Identity resolution + physical split-repair (Feature 067).

Two operator steps, both idempotent and dry-run-capable:

- ``resolve_identities``: promote UNMAPPED id_mappings to MAPPED/CONFLICT via ``classify_identity``
  (structural id equality + name/birth corroboration). Sticky: never re-evaluates a row that is
  already MAPPED/CONFLICT/REJECTED.
- ``repair_splits``: for each MAPPED surrogate whose master row still exists, physically re-key
  its rows to the canonical id **as one atomic per-pair transaction** (all-or-nothing; a single
  collision skips the whole pair), then delete the orphan master. Derived rows (race_predictions /
  feature_snapshots) are re-keyed for FK integrity; their stale VALUES stay as legacy audit and are
  superseded by a fresh predict-backfill --force run. recommendations' JSON horse_id is physically
  canonicalized (backtest matches it directly). No schema change; audited to ingestion_jobs.
"""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass, field

from horseracing_db.enums import EntityType, JobStatus, MappingStatus, Source
from horseracing_db.models import (
    Horse,
    IdMapping,
    IngestionJob,
    Jockey,
    Trainer,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import SURROGATE_PREFIX
from .identity import classify_identity

_MASTER = {
    EntityType.HORSE: (Horse, "horse_id"),
    EntityType.JOCKEY: (Jockey, "jockey_id"),
    EntityType.TRAINER: (Trainer, "trainer_id"),
}

#: horse master attribute columns whose loss on surrogate-delete must be gated (codex#3).
#: Feature-relevant static attributes only. The raw pedigree *ID* columns (sire_id/dam_id/
#: damsire_id) are deliberately excluded: they are ~0% populated on JRA-VAN canonical rows, are
#: never model features (026 keys pedigree on sire_NAME), and the surrogate's own values are
#: themselves ``nk:`` surrogate ids — so "losing" them on delete carries no information. Other
#: horses' references TO this surrogate as a pedigree parent are re-keyed separately (_rekey_horse).
_HORSE_ATTRS = (
    "sex", "birth_year",
    "sire_name", "dam_name", "damsire_name",
    "owner_name", "breeder_name", "sire_line", "damsire_line",
)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _entities(entity: str) -> list[str]:
    return list(EntityType.ALL) if entity == "all" else [entity]


# --- US3 / Foundational: resolve UNMAPPED -> MAPPED/CONFLICT --------------------------------------
@dataclass
class ResolveReport:
    resolved: dict[str, int] = field(default_factory=dict)
    conflicts: dict[str, int] = field(default_factory=dict)
    insufficient: dict[str, int] = field(default_factory=dict)
    examples: dict[str, list[str]] = field(default_factory=dict)
    dry_run: bool = False


def resolve_identities(
    session: Session, *, entity: str = "all", dry_run: bool = False
) -> ResolveReport:
    """Promote UNMAPPED netkeiba id_mappings to MAPPED/CONFLICT by identity (sticky, idempotent)."""
    rep = ResolveReport(dry_run=dry_run)
    for ent in _entities(entity):
        master_model, id_col = _MASTER[ent]
        rep.resolved.setdefault(ent, 0)
        rep.conflicts.setdefault(ent, 0)
        rep.insufficient.setdefault(ent, 0)
        rep.examples.setdefault(ent, [])
        rows = session.execute(
            select(IdMapping).where(
                IdMapping.entity_type == ent,
                IdMapping.source == Source.NETKEIBA,
                IdMapping.mapping_status == MappingStatus.UNMAPPED,
            )
        ).scalars().all()
        for m in rows:
            source_id = m.source_id
            canonical_row = session.get(master_model, source_id)
            surrogate_row = session.get(master_model, f"{SURROGATE_PREFIX}{source_id}")
            cand_name = getattr(surrogate_row, _name_col(ent), None) if surrogate_row else None
            cand_by = getattr(surrogate_row, "birth_year", None) if (
                surrogate_row and ent == EntityType.HORSE
            ) else None
            res = classify_identity(
                entity_type=ent, source_id=source_id, candidate_name=cand_name,
                canonical_row=canonical_row, candidate_birth_year=cand_by,
            )
            if res.status == MappingStatus.MAPPED:
                rep.resolved[ent] += 1
                if len(rep.examples[ent]) < 8:
                    rep.examples[ent].append(f"{source_id} -> {res.canonical_id} ({res.reason})")
                if not dry_run:
                    m.canonical_id = res.canonical_id
                    m.mapping_status = MappingStatus.MAPPED
                    m.resolved_at = _now()
                    m.resolution_note = res.reason
            elif res.status == MappingStatus.CONFLICT:
                rep.conflicts[ent] += 1
                if len(rep.examples[ent]) < 8:
                    rep.examples[ent].append(f"CONFLICT {source_id}: {res.reason}")
                if not dry_run:
                    m.mapping_status = MappingStatus.CONFLICT
                    m.resolution_note = res.reason
            else:  # UNMAPPED (no canonical / insufficient evidence) — leave status, note reason
                if "insufficient" in res.reason:
                    rep.insufficient[ent] += 1
                    if not dry_run:
                        m.resolution_note = res.reason
    if not dry_run:
        session.commit()
    return rep


def _name_col(entity: str) -> str:
    return {EntityType.HORSE: "horse_name", EntityType.JOCKEY: "jockey_name",
            EntityType.TRAINER: "trainer_name"}[entity]


# --- US1: physical split repair ------------------------------------------------------------------
@dataclass
class RepairReport:
    rekeyed_rows: dict[str, int] = field(default_factory=dict)
    orphans_deleted: dict[str, int] = field(default_factory=dict)
    collisions: dict[str, int] = field(default_factory=dict)
    held: dict[str, int] = field(default_factory=dict)  # pre-delete attribute gate blocked
    errors: list[str] = field(default_factory=list)
    affected_from: datetime.date | None = None
    pairs_processed: int = 0
    dry_run: bool = False


def _scalar(session: Session, sql: str, **params) -> int:
    return session.execute(text(sql), params).scalar() or 0


def _horse_collision(session: Session, s: str, c: str) -> bool:
    # PK (race_id, horse_id) for race_horses/race_results; (prediction_run_id, horse_id) for the
    # derived tables — a target row already existing under C means we must skip the whole pair.
    for tbl, key in (("race_horses", "race_id"), ("race_results", "race_id"),
                     ("race_predictions", "prediction_run_id"),
                     ("feature_snapshots", "prediction_run_id")):
        n = _scalar(
            session,
            f"select count(*) from {tbl} a where a.horse_id=:s and exists "
            f"(select 1 from {tbl} b where b.{key}=a.{key} and b.horse_id=:c)",
            s=s, c=c,
        )
        if n:
            return True
    return False


def _horse_attr_gate(canonical: Horse, surrogate: Horse) -> list[str]:
    """Columns where canonical is NULL but surrogate has a value (would be lost on delete)."""
    return [
        col for col in _HORSE_ATTRS
        if getattr(canonical, col) is None and getattr(surrogate, col) is not None
    ]


def _min_race_date_horse(session: Session, s: str) -> datetime.date | None:
    return session.execute(
        text("select min(r.race_date) from race_horses rh join races r on r.race_id=rh.race_id "
             "where rh.horse_id=:s"), {"s": s},
    ).scalar()


def _min_race_date_person(session: Session, col: str, s: str) -> datetime.date | None:
    return session.execute(
        text(f"select min(r.race_date) from race_horses rh join races r on r.race_id=rh.race_id "
             f"where rh.{col}=:s"), {"s": s},
    ).scalar()


def _rekey_horse(session: Session, s: str, c: str, rep: RepairReport) -> None:
    def upd(sql: str) -> int:
        return session.execute(text(sql), {"s": s, "c": c}).rowcount

    rep.rekeyed_rows["race_horses"] = rep.rekeyed_rows.get("race_horses", 0) + upd(
        "update race_horses set horse_id=:c where horse_id=:s")
    rep.rekeyed_rows["race_results"] = rep.rekeyed_rows.get("race_results", 0) + upd(
        "update race_results set horse_id=:c where horse_id=:s")
    rep.rekeyed_rows["race_predictions"] = rep.rekeyed_rows.get("race_predictions", 0) + upd(
        "update race_predictions set horse_id=:c where horse_id=:s")
    rep.rekeyed_rows["feature_snapshots"] = rep.rekeyed_rows.get("feature_snapshots", 0) + upd(
        "update feature_snapshots set horse_id=:c where horse_id=:s")
    ped = 0
    for col in ("sire_id", "dam_id", "damsire_id"):
        ped += upd(f"update horses set {col}=:c where {col}=:s")
    rep.rekeyed_rows["horses.pedigree_id"] = rep.rekeyed_rows.get("horses.pedigree_id", 0) + ped
    rec = session.execute(
        text("update recommendations set selection=jsonb_set(selection,'{horse_id}',"
             "to_jsonb(cast(:c as text))) where bet_type='win' "
             "and selection->>'horse_id'=:s"),
        {"s": s, "c": c},
    ).rowcount
    rep.rekeyed_rows["recommendations"] = rep.rekeyed_rows.get("recommendations", 0) + rec


def _residual_horse(session: Session, s: str) -> int:
    total = 0
    for tbl in ("race_horses", "race_results", "race_predictions", "feature_snapshots"):
        total += _scalar(session, f"select count(*) from {tbl} where horse_id=:s", s=s)
    for col in ("sire_id", "dam_id", "damsire_id"):
        total += _scalar(session, f"select count(*) from horses where {col}=:s", s=s)
    return total


def repair_splits(
    session: Session, *, entity: str = "all", dry_run: bool = False, limit: int | None = None
) -> RepairReport:
    """Physically re-key MAPPED surrogates to canonical (atomic per pair) and delete orphans."""
    rep = RepairReport(dry_run=dry_run)
    affected: list[datetime.date] = []
    for ent in _entities(entity):
        master_model, id_col = _MASTER[ent]
        rep.collisions.setdefault(ent, 0)
        rep.held.setdefault(ent, 0)
        rep.orphans_deleted.setdefault(ent, 0)
        mappings = session.execute(
            select(IdMapping).where(
                IdMapping.entity_type == ent,
                IdMapping.source == Source.NETKEIBA,
                IdMapping.mapping_status == MappingStatus.MAPPED,
                IdMapping.canonical_id.isnot(None),
            )
        ).scalars().all()
        for m in mappings:
            if limit is not None and rep.pairs_processed >= limit:
                break
            c = m.canonical_id
            s = f"{SURROGATE_PREFIX}{m.source_id}"
            surrogate = session.get(master_model, s)
            if surrogate is None:
                continue  # already re-keyed (idempotent no-op)
            canonical = session.get(master_model, c)
            if canonical is None:
                rep.errors.append(f"{ent}: canonical {c} missing for {s}")
                continue
            rep.pairs_processed += 1
            sp = session.begin_nested()
            try:
                if ent == EntityType.HORSE:
                    if _horse_collision(session, s, c):
                        rep.collisions[ent] += 1
                        sp.rollback()
                        continue
                    gate = _horse_attr_gate(canonical, surrogate)
                    if gate:
                        rep.held[ent] += 1
                        rep.errors.append(f"held horse {s}: canonical missing {gate}")
                        sp.rollback()
                        continue
                    d = _min_race_date_horse(session, s)
                    _rekey_horse(session, s, c, rep)
                    if _residual_horse(session, s) != 0:
                        raise RuntimeError(f"residual refs remain for {s}")
                else:  # jockey / trainer: only race_horses.<col>, no PK collision possible
                    col = "jockey_id" if ent == EntityType.JOCKEY else "trainer_id"
                    d = _min_race_date_person(session, col, s)
                    n = session.execute(
                        text(f"update race_horses set {col}=:c where {col}=:s"), {"s": s, "c": c}
                    ).rowcount
                    rep.rekeyed_rows[f"race_horses.{col}"] = (
                        rep.rekeyed_rows.get(f"race_horses.{col}", 0) + n
                    )
                # delete the now-orphan surrogate master row
                session.delete(surrogate)
                session.flush()
                rep.orphans_deleted[ent] += 1
                if d is not None:
                    affected.append(d)
                if dry_run:
                    sp.rollback()  # discard all writes; counts already captured
                else:
                    sp.commit()
            except Exception as exc:  # noqa: BLE001 — record and continue with next pair
                sp.rollback()
                rep.errors.append(f"{ent} {s}: {exc}")
        if limit is not None and rep.pairs_processed >= limit:
            break

    if affected:
        rep.affected_from = min(affected)
    if not dry_run:
        _persist_audit(session, rep, entity)
        session.commit()
    return rep


def _persist_audit(session: Session, rep: RepairReport, entity: str) -> None:
    """Record the repair run to ingestion_jobs (no schema change, constitution V)."""
    mapping_hash = hashlib.sha256(
        f"{sorted(rep.rekeyed_rows.items())}|{sorted(rep.orphans_deleted.items())}".encode()
    ).hexdigest()[:16]
    session.add(IngestionJob(
        source=Source.NETKEIBA,
        job_type="repair_splits",
        scope=entity,
        status=JobStatus.SUCCEEDED if not rep.errors else JobStatus.PARTIAL,
        started_at=_now(),
        completed_at=_now(),
        processed_rows=rep.pairs_processed,
        error_count=len(rep.errors),
        summary={
            "rekeyed_rows": rep.rekeyed_rows,
            "orphans_deleted": rep.orphans_deleted,
            "collisions": rep.collisions,
            "held": rep.held,
            "affected_from": rep.affected_from.isoformat() if rep.affected_from else None,
            "mapping_hash": mapping_hash,
            "tool": "067-repair_splits",
            "errors": rep.errors[:50],
        },
    ))
