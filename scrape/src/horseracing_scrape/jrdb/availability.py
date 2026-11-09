"""When did a JRDB-supplied value become available? (Feature 178 FR-010, data-model §3a)

The provenance tables carry no ``published_at`` column (176's columns are source / observed_at /
official_at / ingestion_job_id only). The availability time is DERIVED:

``race_*_field_provenance.ingestion_job_id`` → ``ingestion_jobs.summary.files[]`` (the JRDB jobs
record every file they processed with ``kind`` / ``published_at`` / ``fetched_at``) → the file whose
``kind`` feeds that field group → its ``published_at`` (HTTP Last-Modified). When the file carried
no Last-Modified the answer is only an upper bound: "available no later than ``fetched_at``".

Rule (a) of §3a: the provenance job id is the job that ADOPTED the current value, so a later
re-ingest that changes nothing leaves it alone — the derivation keeps pointing at the right file.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from horseracing_db.models import IngestionJob
from horseracing_db.models.provenance import RaceFieldProvenance, RaceHorseFieldProvenance
from sqlalchemy.orm import Session

from .. import precedence as P

#: field group → the JRDB file kinds that supply it (data-model §3a)
KIND_FOR_GROUP: dict[str, tuple[str, ...]] = {
    P.GROUP_ENTRY_STATIC: ("KYI",),
    P.GROUP_JOCKEY: ("KYI",),
    P.GROUP_TRAINER: ("KYI",),
    P.GROUP_ENTRY_STATUS: ("KYI", "TYB", "SED"),
    P.GROUP_BODY_WEIGHT: ("TYB", "SED"),
    P.GROUP_RACE_STATIC: ("BAC",),
    P.GROUP_POST_TIME: ("BAC",),
    "results": ("SED",),
    "laps": ("SRB",),
    "win_payouts": ("HJC",),
    "exotic_payouts": ("HJC",),
}

#: ``race_*_field_provenance.field_group`` values that live on the race-level table
RACE_LEVEL_GROUPS: frozenset[str] = frozenset(
    {P.GROUP_RACE_STATIC, P.GROUP_POST_TIME, "results", "laps", "win_payouts", "exotic_payouts"}
)


@dataclass(frozen=True)
class Availability:
    """``published_at`` is the answer when known; otherwise ``fetched_at`` is the upper bound."""

    source: str
    ingestion_job_id: uuid.UUID | None
    kind: str | None
    published_at: datetime.datetime | None
    fetched_at: datetime.datetime | None
    #: what ``fetched_at`` is: ``http_fetch`` (a real download time) or ``file_mtime`` (an
    #: archive file's modification time — an upper bound at best, never proof of availability)
    fetched_at_basis: str | None = None

    @property
    def known(self) -> bool:
        return self.published_at is not None

    @property
    def upper_bound(self) -> datetime.datetime | None:
        return self.published_at or self.fetched_at


def _parse(ts) -> datetime.datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime.datetime):
        return ts
    return datetime.datetime.fromisoformat(str(ts))


def available_at(
    session: Session, race_id: str, field_group: str, horse_id: str | None = None
) -> Availability | None:
    """The availability of the CURRENT value of ``field_group`` on a race (or a race × horse).
    ``None`` when the group has no provenance row (a JRA-VAN-era value, or never written by a
    route that records provenance)."""

    if field_group in RACE_LEVEL_GROUPS or horse_id is None:
        row = session.get(RaceFieldProvenance, (race_id, field_group))
    else:
        row = session.get(RaceHorseFieldProvenance, (race_id, horse_id, field_group))
    if row is None:
        return None
    job = session.get(IngestionJob, row.ingestion_job_id) if row.ingestion_job_id else None
    files = ((job.summary or {}).get("files") or []) if job is not None else []
    wanted = KIND_FOR_GROUP.get(field_group, ())
    chosen = next((f for f in files if f.get("kind") in wanted), None)
    if chosen is None:
        return Availability(row.source, row.ingestion_job_id, None, None, None)
    return Availability(
        row.source,
        row.ingestion_job_id,
        chosen.get("kind"),
        _parse(chosen.get("published_at")),
        _parse(chosen.get("fetched_at")),
        chosen.get("fetched_at_basis"),
    )


__all__ = ["KIND_FOR_GROUP", "RACE_LEVEL_GROUPS", "Availability", "available_at"]
