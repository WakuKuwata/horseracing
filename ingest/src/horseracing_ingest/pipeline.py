"""Year-file ingestion orchestration with ingestion_jobs audit (research R8)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path

from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import IngestionJob
from horseracing_db.validation import is_in_ingest_scope
from sqlalchemy.orm import Session

from .mapping import MappingError, to_core_records
from .parser import ParsedRow, RowError, parse_rows
from .upsert import upsert_core

_CHECKPOINT_EVERY = 1000


@dataclass
class IngestSummary:
    year: int | None
    races: int = 0
    race_horses: int = 0
    race_results: int = 0
    skipped: bool = False
    skipped_rows: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _resolve_year(path: Path) -> int | None:
    if path.stem.isdigit():
        return int(path.stem)
    for item in parse_rows(path):
        if isinstance(item, ParsedRow):
            from . import layout

            raw = item.fields[layout.RACE_DATE].split(".")
            if raw and raw[0].strip().isdigit():
                return int(raw[0].strip())
        break
    return None


def ingest_year(session: Session, path: str | Path, *, resume_from_line: int = 0) -> IngestSummary:
    path = Path(path)
    year = _resolve_year(path)

    # --- 2007 boundary (validation.is_in_ingest_scope is the only source of truth) ---
    if year is not None and not is_in_ingest_scope(datetime.date(year, 1, 1)):
        n = sum(1 for _ in parse_rows(path))
        job = IngestionJob(
            source=Source.JRA_VAN, job_type="historical_year", scope="year",
            scope_value=str(year), status=JobStatus.SKIPPED,
            processed_rows=0, skipped_rows=n, error_count=0,
            started_at=_now(), completed_at=_now(),
            summary={"reason": "pre-2007 out of ingest scope"},
        )
        session.add(job)
        session.commit()
        return IngestSummary(year, skipped=True, skipped_rows=n)

    job = IngestionJob(
        source=Source.JRA_VAN, job_type="historical_year", scope="year",
        scope_value=str(year) if year is not None else None,
        status=JobStatus.RUNNING, started_at=_now(),
    )
    session.add(job)
    session.flush()

    summary = IngestSummary(year)
    race_ids: set[str] = set()
    last_line = resume_from_line
    processed = 0

    for item in parse_rows(path):
        if item.line_no <= resume_from_line:
            continue
        last_line = item.line_no
        if isinstance(item, RowError):
            summary.errors += 1
            summary.error_messages.append(f"L{item.line_no}: {item.reason}")
            continue
        processed += 1
        try:
            rec = to_core_records(item)
        except MappingError as exc:
            summary.errors += 1
            summary.error_messages.append(f"L{item.line_no}: {exc}")
            continue
        upsert_core(session, rec)
        race_ids.add(rec.race_id)
        summary.race_horses += 1
        if rec.race_result is not None:
            summary.race_results += 1
        if processed % _CHECKPOINT_EVERY == 0:
            job.checkpoint = str(last_line)
            job.processed_rows = processed
            session.flush()

    summary.races = len(race_ids)
    job.status = JobStatus.PARTIAL if summary.errors else JobStatus.SUCCEEDED
    job.processed_rows = processed
    job.error_count = summary.errors
    job.checkpoint = str(last_line)
    job.completed_at = _now()
    job.error_message = "\n".join(summary.error_messages[:50]) or None
    job.summary = {
        "races": summary.races,
        "race_horses": summary.race_horses,
        "race_results": summary.race_results,
    }
    session.commit()
    return summary
