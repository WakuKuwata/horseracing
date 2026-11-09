"""File identity and versioning (Feature 178, T030; research D9; data-model §2).

* :func:`parse_file_name` — what a delivered file *is* from its name (kind, layout version,
  data date, variant).
* :func:`classify_version` — the ONLY automatic orderings between two differing-content versions
  of the same (kind, data_date, variant): 速報→正式 (SED/SEC, by edition) and 日次→バックナンバー
  (variant). Everything else is ``held``. ``published_at`` is never consulted (codex D15 #3).
* :func:`record_fetched` / :func:`mark_superseded` / :func:`mark_held` — the ``jrdb_files`` rows.
  The same sha is one row: a re-fetch changes nothing, not even ``fetched_at``.
* :func:`save_extracted` — the decoded text under ``artifacts/jrdb/<kind>/<yyyymmdd>/``
  (git-ignored).
"""

from __future__ import annotations

import datetime
import hashlib
import re
from pathlib import Path
from typing import Literal

from horseracing_db.models.jrdb import JrdbFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import JrdbDecodeError
from .layout import CURRENT_VERSION
from .models import JrdbFileMeta

Verdict = Literal["same", "apply", "supersede", "held"]
Variant = Literal["daily", "venue_e", "venue_w", "venue_l", "archive"]

_DAILY_RE = re.compile(r"^([A-Z]{2,3})(\d{6})\.txt$", re.IGNORECASE)
_VENUE_RE = re.compile(r"^TYB_([EWL])\.txt$", re.IGNORECASE)
#: legacy kinds decode with their own layouts (not shipped in this feature): version by kind letter
_LEGACY_VERSIONS = {"KYH": "10", "KYG": "9", "SEC": "3"}
SED_FORMAL_WEEKDAY = 3  # Thursday


def _date6(yymmdd: str, *, reference: datetime.date | None = None) -> datetime.date:
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    ref = reference or datetime.date.today()
    base = ref.year - ref.year % 100 + yy
    year = min((base - 100, base, base + 100), key=lambda y: abs(y - ref.year))
    try:
        return datetime.date(year, mm, dd)
    except ValueError:
        raise JrdbDecodeError("file name date is not a calendar date") from None


def parse_file_name(
    name: str,
    *,
    variant: Variant = "daily",
    data_date: datetime.date | None = None,
    reference: datetime.date | None = None,
) -> tuple[str, str | None, datetime.date, str]:
    """``KYI150712.txt`` → ``("KYI", "11", 2015-07-12, variant)``;
    ``TYB_E.txt`` needs ``data_date``."""

    base = name.rsplit("/", 1)[-1]
    m = _VENUE_RE.match(base)
    if m:
        if data_date is None:
            raise JrdbDecodeError("a per-venue TYB file needs an explicit data date")
        return "TYB", CURRENT_VERSION["TYB"], data_date, f"venue_{m.group(1).lower()}"
    m = _DAILY_RE.match(base)
    if not m:
        raise JrdbDecodeError("file name is not <KIND>yymmdd.txt")
    kind = m.group(1).upper()
    day = _date6(m.group(2), reference=reference)
    version = CURRENT_VERSION.get(kind) or _LEGACY_VERSIONS.get(kind)
    return kind, version, day, variant


def detect_record_length(raw: bytes) -> int | None:
    """Bytes per record INCLUDING the 2-byte terminator, from the first line (None if empty)."""

    nl = raw.find(b"\n")
    if nl < 0:
        return None
    line = raw[:nl]
    if line.endswith(b"\r"):
        line = line[:-1]
    return len(line) + 2


def sed_edition(data_date: datetime.date, fetched_at: datetime.datetime) -> str:
    """速報 (fetched on the race day itself) vs 正式 (from the following Thursday 16:00 on)."""

    local = fetched_at.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    days_after = (local.date() - data_date).days
    offset = (SED_FORMAL_WEEKDAY - data_date.weekday()) % 7 or 7
    formal_from = datetime.datetime.combine(
        data_date + datetime.timedelta(days=offset), datetime.time(16, 0), tzinfo=local.tzinfo
    )
    if days_after <= 0 or local < formal_from:
        return "sokuho"
    return "formal"


def build_meta(
    name: str,
    raw: bytes,
    *,
    variant: Variant,
    fetched_at: datetime.datetime,
    data_date: datetime.date | None = None,
) -> JrdbFileMeta:
    kind, version, day, var = parse_file_name(name, variant=variant, data_date=data_date)
    rl = detect_record_length(raw) or 0
    edition = None
    if kind in ("SED", "SEC"):
        edition = "formal" if var == "archive" else sed_edition(day, fetched_at)
    return JrdbFileMeta(
        kind=kind,
        version=version,
        data_date=day,
        variant=var,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
        record_length=rl,
        edition=edition,
    )


def classify_version(existing: JrdbFileMeta | None, incoming: JrdbFileMeta) -> Verdict:
    if existing is None:
        return "apply"
    if existing.sha256 == incoming.sha256:
        return "same"
    if (existing.kind, existing.data_date) != (incoming.kind, incoming.data_date):
        raise ValueError("classify_version compares versions of the same (kind, data_date)")
    # 日次 → バックナンバー: corrections land in the back-number (D9 b)
    if existing.variant != "archive" and incoming.variant == "archive":
        return "supersede"
    if existing.variant == "archive" and incoming.variant != "archive":
        return "held"
    # 速報 → 正式 (SED/SEC): defined by the delivery schedule (D9 a)
    if existing.kind in ("SED", "SEC"):
        if existing.edition == "sokuho" and incoming.edition == "formal":
            return "supersede"
        return "held"
    return "held"


def version_family(variant: str) -> str:
    """Versions that compete for the same (kind, data_date): the 日次 and the バックナンバー are
    one family (D9: 日次→バックナンバー supersedes), the TYB 場別上書き files are each their own
    (they coexist with the 日次累積 file and with each other)."""

    return "day" if variant in ("daily", "archive") else variant


def find_versions(
    session: Session, kind: str, data_date: datetime.date, variant: str
) -> list[JrdbFile]:
    """Every recorded version of ``(kind, data_date)`` in the same version family as
    ``variant``, oldest fetch first. R7 (2026-11-09): filtering on the exact variant hid the
    year pack from a later 日次 file (and vice versa), so the 日次→バックナンバー rule never ran."""

    rows = session.scalars(
        select(JrdbFile)
        .where(JrdbFile.kind == kind, JrdbFile.data_date == data_date)
        .order_by(JrdbFile.fetched_at)
    )
    fam = version_family(variant)
    return [r for r in rows if version_family(r.variant) == fam]


def record_fetched(
    session: Session,
    meta: JrdbFileMeta,
    *,
    source_url_sha256: str,
    fetched_at: datetime.datetime,
    published_at: datetime.datetime | None,
) -> tuple[JrdbFile, bool]:
    """Insert a ``fetched`` row for this content, or return the existing row untouched."""

    existing = session.scalar(
        select(JrdbFile).where(
            JrdbFile.kind == meta.kind,
            JrdbFile.data_date == meta.data_date,
            JrdbFile.variant == meta.variant,
            JrdbFile.sha256 == meta.sha256,
        )
    )
    if existing is not None:
        return existing, False
    row = JrdbFile(
        kind=meta.kind,
        data_date=meta.data_date,
        variant=meta.variant,
        sha256=meta.sha256,
        size_bytes=meta.size,
        record_length=meta.record_length,
        source_url_sha256=source_url_sha256,
        published_at=published_at,
        fetched_at=fetched_at,
        status="fetched",
        race_status={},
    )
    session.add(row)
    session.flush()
    return row, True


def mark_superseded(session: Session, row: JrdbFile) -> None:
    row.status = "superseded"
    session.flush()


def mark_held(session: Session, row: JrdbFile) -> None:
    if row.status in ("applied", "partially_applied"):
        raise ValueError("an applied file cannot be held")
    row.status = "held"
    session.flush()


def mark_rejected(session: Session, row: JrdbFile) -> None:
    row.status = "rejected"
    session.flush()


def save_extracted(artifacts_dir: Path | str, meta: JrdbFileMeta, name: str, raw: bytes) -> Path:
    target = (
        Path(artifacts_dir)
        / meta.kind
        / meta.data_date.strftime("%Y%m%d")
        / name.rsplit("/", 1)[-1]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != meta.sha256:
        # a differing version of the same name: keep both, suffix by sha
        target = target.with_name(f"{target.stem}.{meta.sha256[:12]}{target.suffix}")
    if not target.exists():
        target.write_bytes(raw)
    return target


__all__ = [
    "Variant",
    "Verdict",
    "build_meta",
    "classify_version",
    "detect_record_length",
    "find_versions",
    "mark_held",
    "mark_rejected",
    "version_family",
    "mark_superseded",
    "parse_file_name",
    "record_fetched",
    "save_extracted",
    "sed_edition",
]
