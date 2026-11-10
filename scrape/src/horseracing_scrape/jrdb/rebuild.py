"""段 3b — rebuild the JRDB canonical DB from the archive (Feature 178 US3, T059; FR-020/021).

A SEPARATE database with the same migration head, filled with ``source='jrdb'`` only, 1999〜現在,
through the very writers the daily route uses (``upsert_jrdb_*`` under ``profile=jrdb_canonical``):
the four JRDB-defined columns land in JRDB's definition for every year, no JRA-VAN value is mixed
in, and the current DB is only READ (for the year-by-year comparison).

Order per day: entries (BAC+KYI+UKC) → prerace (TYB) → results (SED 正式版 + SRB) → payouts (HJC).
Each archive file gets a ``jrdb_files`` row in the target DB (``variant='archive'``), so the
summary's ``files[]`` (data-model §3a) points at real rows, and a re-run of the same file only
re-processes the races it did not apply (``unapplied_races``).

Safety: ``db_url`` must differ from ``reference_db_url`` and from the process ``DATABASE_URL``
(the current DB is never the target), and both DBs must sit on the same ``alembic_version``.

Report: ``compare-<year>.json`` with races / entries / results / payout_rows counts in both DBs,
the target's fill rates of the JRDB-defined and repaired columns, and ``blocking_years`` — a
year where JRDB has FEWER races, entries or results than the current DB (FR-020: 理由を特定する
まで進まない) makes the exit code non-zero.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from horseracing_db.session import create_db_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..pipeline import _run_job
from ..upsert import Counts
from . import files as jfiles
from . import upsert_jrdb as W
from .cli_write import DayFile, _apply_status, _files_summary
from .decode import decode_file
from .errors import JrdbError, JrdbStartupRefused
from .profile import JRDB_CANONICAL

SOURCE = "jrdb"
STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("entries", ("BAC", "KYI", "UKC")),
    ("prerace", ("TYB",)),
    ("results", ("SED", "SRB")),
    ("payouts", ("HJC",)),
)
#: every compared key set blocks when the current DB has a key JRDB lacks (FR-020)
BLOCKING_COUNTS: tuple[str, ...] = ("races", "entries", "results", "payout_rows", "laps")

_COUNT_SQL = {
    "races": "SELECT count(*) FROM races WHERE extract(year FROM race_date) = :y",
    "entries": """SELECT count(*) FROM race_horses rh JOIN races r USING (race_id)
                  WHERE extract(year FROM r.race_date) = :y""",
    "results": """SELECT count(*) FROM race_results rr JOIN races r USING (race_id)
                  WHERE extract(year FROM r.race_date) = :y""",
    "payout_rows": """SELECT count(*) FROM official_win_payouts p JOIN races r USING (race_id)
                      WHERE extract(year FROM r.race_date) = :y""",
    "laps": """SELECT count(*) FROM race_laps l JOIN races r USING (race_id)
               WHERE extract(year FROM r.race_date) = :y""",
}
#: (table, column) → fill rate in the target: the JRDB-defined columns (FR-021) + the repaired ones
_FILL = (
    ("race_results", "first_3f"),
    ("race_results", "corner_orders"),
    ("race_results", "last_3f"),
    ("race_horses", "running_style"),
    ("race_horses", "odds"),
    ("race_horses", "weight"),
    ("race_horses", "jockey_id"),
    ("race_horses", "trainer_id"),
    ("horses", "sire_line"),
    ("horses", "damsire_line"),
    ("horses", "owner_name"),
    ("horses", "breeder_name"),
    ("races", "post_time"),
    ("races", "race_class"),
)
_FILL_SQL = {
    "races": """SELECT count(*), count({c}) FROM races
                WHERE extract(year FROM race_date) = :y""",
    "race_horses": """SELECT count(*), count(rh.{c}) FROM race_horses rh
                      JOIN races r USING (race_id) WHERE extract(year FROM r.race_date) = :y""",
    "race_results": """SELECT count(*), count(rr.{c}) FROM race_results rr
                       JOIN races r USING (race_id) WHERE extract(year FROM r.race_date) = :y""",
    "horses": """SELECT count(*), count(h.{c}) FROM race_horses rh JOIN races r USING (race_id)
                 JOIN horses h USING (horse_id) WHERE extract(year FROM r.race_date) = :y""",
}


def _url_key(url: str | None) -> str | None:
    """Compare URLs by content, never print them (they carry credentials)."""

    return hashlib.sha256(url.strip().encode()).hexdigest() if url else None


def assert_distinct_targets(db_url: str, reference_db_url: str, env_url: str | None) -> None:
    if _url_key(db_url) == _url_key(reference_db_url):
        raise JrdbStartupRefused("same_database", "--db-url equals --reference-db-url")
    if env_url and _url_key(db_url) == _url_key(env_url):
        raise JrdbStartupRefused("same_database", "--db-url equals the process DATABASE_URL")


def db_identity(engine: Engine) -> tuple[str, int, str]:
    """(postmaster start time, database OID, database name): the same database reached through
    two URL spellings (user, driver, host alias, parameters) yields the same identity."""

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT pg_postmaster_start_time()::text, "
                "(SELECT oid FROM pg_database WHERE datname = current_database()), "
                "current_database()"
            )
        ).one()
    return (str(row[0]), int(row[1]), str(row[2]))


def assert_distinct_databases(target: Engine, reference: Engine, env_url: str | None) -> None:
    """codex D23 Q2 #1: compare the CONNECTED databases, not the URL strings."""

    t = db_identity(target)
    if t == db_identity(reference):
        raise JrdbStartupRefused("same_database", "--db-url reaches the reference database")
    if env_url:
        env = create_db_engine(env_url)
        try:
            same = db_identity(env) == t
        except Exception:  # noqa: BLE001 — an unreachable env DB cannot be the target
            same = False
        finally:
            env.dispose()
        if same:
            raise JrdbStartupRefused("same_database", "--db-url reaches the process DATABASE_URL")


_FOREIGN_ROWS_SQL = (
    ("horses", "SELECT count(*) FROM horses WHERE data_source IS DISTINCT FROM 'jrdb'"),
    ("race_laps", "SELECT count(*) FROM race_laps WHERE source IS DISTINCT FROM 'jrdb'"),
    (
        "race_field_provenance",
        "SELECT count(*) FROM race_field_provenance WHERE source <> 'jrdb'",
    ),
    (
        "race_horse_field_provenance",
        "SELECT count(*) FROM race_horse_field_provenance WHERE source <> 'jrdb'",
    ),
    ("ingestion_jobs", "SELECT count(*) FROM ingestion_jobs WHERE source IS DISTINCT FROM 'jrdb'"),
    ("exotic_odds", "SELECT count(*) FROM exotic_odds WHERE source IS DISTINCT FROM 'jrdb'"),
    (
        "official_win_payouts",
        "SELECT count(*) FROM official_win_payouts WHERE source <> 'jrdb_result'",
    ),
)


def assert_target_is_jrdb_only(target: Engine) -> None:
    """codex D23 Q2 #5: the canonical DB holds ``source='jrdb'`` rows only (FR-021). A target that
    already carries another route's rows is not this rebuild's (nor a resumable one)."""

    with target.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        foreign = {
            table: int(conn.execute(text(sql)).scalar_one()) for table, sql in _FOREIGN_ROWS_SQL
        }
        conn.rollback()
    bad = {k: v for k, v in foreign.items() if v}
    if bad:
        raise JrdbStartupRefused("target_not_jrdb_only", f"non-JRDB rows in target: {bad}")


def alembic_head(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


def assert_same_schema(target: Engine, reference: Engine) -> str:
    a, b = alembic_head(target), alembic_head(reference)
    if a is None or a != b:
        raise JrdbStartupRefused("schema_mismatch", f"target alembic {a!r} != reference {b!r}")
    return a


# --------------------------------------------------------------------------- loading


@dataclass(frozen=True)
class ArchiveFile:
    path: Path
    kind: str
    day: datetime.date
    raw: bytes
    mtime: datetime.datetime


def load_archive_days(
    files_dir: Path, date_from: datetime.date, date_to: datetime.date
) -> tuple[dict[datetime.date, list[ArchiveFile]], list[str]]:
    """``{day: [files]}`` for every daily archive file in range (persons files KZA/CZA/KSA/CSA are
    not day files and are left to ``jrdb-persons``)."""

    by_day: dict[datetime.date, list[ArchiveFile]] = {}
    skipped: list[str] = []
    years = range(date_from.year, date_to.year + 1)
    for path in sorted(Path(files_dir).rglob("*.txt")):
        parsed = None
        plain_name = plain_file_name(path)
        for year in years:
            try:
                kind, _v, day, _var = jfiles.parse_file_name(
                    plain_name, reference=datetime.date(year, 7, 1)
                )
            except JrdbError:
                break
            if day.year == year:
                parsed = (kind, day)
                break
        if parsed is None:
            continue
        kind, day = parsed
        if not (date_from <= day <= date_to):
            continue
        if kind not in {k for _s, kinds in STEPS for k in kinds}:
            skipped.append(path.name)
            continue
        by_day.setdefault(day, []).append(
            ArchiveFile(
                path,
                kind,
                day,
                path.read_bytes(),
                datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.UTC),
            )
        )
    return by_day, skipped


def plain_file_name(path: Path) -> str:
    """``save_extracted`` keeps a second version of the same name as ``KYI260913.<sha12>.txt``;
    the file's identity (kind, day) is the plain name (R7 2026-11-09: the daily BAC that completed
    a truncated year-pack BAC was invisible to the rebuild)."""

    return path.name.split(".")[0] + ".txt"


def _day_files(
    session: Session, files: Iterable[ArchiveFile], kinds: tuple[str, ...]
) -> list[DayFile]:
    out: list[DayFile] = []
    for f in files:
        if f.kind not in kinds:
            continue
        try:
            meta = jfiles.build_meta(
                plain_file_name(f.path), f.raw, variant="archive", fetched_at=f.mtime
            )
            row, _created = jfiles.record_fetched(
                session,
                meta,
                source_url_sha256=hashlib.sha256(f"rebuild:{f.path.name}".encode()).hexdigest(),
                fetched_at=f.mtime,
                published_at=None,
            )
            if row.status in ("superseded", "held", "rejected"):
                continue  # retired / not yet approved: the writer's rule
            decoded = decode_file(f.raw, meta, profile=JRDB_CANONICAL.name)
            out.append(DayFile(f.path, meta, row, decoded, None))
        except JrdbError as exc:
            out.append(
                DayFile(f.path, None, None, None, f"{f.path.name}: {type(exc).__name__}: {exc}")
            )
    return out


def _rows(files: list[DayFile], kind: str) -> list:
    return [
        r for f in files if f.decoded is not None and f.decoded.kind == kind for r in f.decoded.rows
    ]


# --------------------------------------------------------------------------- one year


@dataclass
class YearReport:
    year: int
    days: int = 0
    jobs: list[dict] = field(default_factory=list)
    counts_jrdb: dict[str, int] = field(default_factory=dict)
    counts_current: dict[str, int] = field(default_factory=dict)
    fill_jrdb: dict[str, dict] = field(default_factory=dict)
    missing_in_jrdb: dict[str, dict] = field(default_factory=dict)
    blocking: list[str] = field(default_factory=list)
    compared_at: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "year": self.year,
            "days": self.days,
            "jobs": self.jobs,
            "compared_at": self.compared_at,
            "counts": {
                k: {"jrdb": self.counts_jrdb.get(k), "current": self.counts_current.get(k)}
                for k in _COUNT_SQL
            },
            "missing_in_jrdb": self.missing_in_jrdb,
            "fill_jrdb": self.fill_jrdb,
            "blocking": self.blocking,
            "notes": self.notes,
        }


def _run_step(
    session: Session,
    step: str,
    kinds,
    day_files: list[DayFile],
    day: datetime.date,
    now,
    person_map,
) -> dict:
    usable = [f for f in day_files if f.decoded is not None]
    statuses = {id(f): W.RaceStatus() for f in usable}

    def work(job_id) -> Counts:
        c = Counts()
        c.extra["files"] = _files_summary(usable)  # type: ignore[assignment]
        c.extra["rows_read"] = sum(f.decoded.n_rows for f in usable)
        c.extra["rows_rejected"] = sum(len(f.decoded.rejected_rows) for f in usable)
        if step == "entries":
            for f in [x for x in usable if x.decoded.kind == "KYI"]:
                rids = {W.race_id_from_key(e.key, file_year=day.year) for e in f.decoded.rows}
                todo = W.unapplied_races(
                    f.row.race_status if f.row else None, sorted(r for r in rids if r)
                )
                c.merge(
                    W.upsert_jrdb_entries(
                        session,
                        _rows(usable, "BAC"),
                        f.decoded.rows,
                        _rows(usable, "UKC"),
                        profile=JRDB_CANONICAL,
                        year=day.year,
                        observed_at=now,
                        job_id=job_id,
                        person_map=person_map,
                        race_status=statuses[id(f)],
                        only_races=todo,
                    )
                )
        elif step == "prerace":
            for f in usable:
                c.merge(
                    W.upsert_jrdb_prerace(
                        session,
                        f.decoded.rows,
                        profile=JRDB_CANONICAL,
                        year=day.year,
                        observed_at=now,
                        job_id=job_id,
                        race_status=statuses[id(f)],
                    )
                )
        elif step == "results":
            for f in [x for x in usable if x.decoded.kind == "SED"]:
                c.merge(
                    W.upsert_jrdb_results(
                        session,
                        f.decoded.rows,
                        _rows(usable, "SRB"),
                        profile=JRDB_CANONICAL,
                        edition=f.meta.edition,
                        year=day.year,
                        observed_at=now,
                        job_id=job_id,
                        race_status=statuses[id(f)],
                        person_map=person_map,
                    )
                )
        elif step == "payouts":
            for f in usable:
                c.merge(
                    W.upsert_jrdb_payouts(
                        session,
                        f.decoded.rows,
                        profile=JRDB_CANONICAL,
                        year=day.year,
                        observed_at=now,
                        job_id=job_id,
                        race_status=statuses[id(f)],
                        html_sha256=f.meta.sha256,
                    )
                )
        for f in usable:
            _apply_status(session, f, statuses[id(f)], job_id, now)
        return c

    summary = _run_job(
        session,
        job_type=f"jrdb_rebuild_{step}",
        scope="date",
        scope_value=day.isoformat(),
        work=work,
        source=SOURCE,
        pass_job_id=True,
    )
    return {
        "day": day.isoformat(),
        "step": step,
        "status": summary.status,
        "processed": summary.processed,
        "written": summary.written,
        "skipped": summary.skipped,
        "errors": summary.errors,
        **{k: v for k, v in (summary.extra or {}).items() if k != "files"},
    }


def _counts(engine: Engine, year: int) -> dict[str, int]:
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        out = {
            k: int(conn.execute(text(sql), {"y": year}).scalar_one())
            for k, sql in _COUNT_SQL.items()
        }
        conn.rollback()
    return out


def _fill(engine: Engine, year: int) -> dict[str, dict]:
    out = {}
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        for table, col in _FILL:
            n, filled = conn.execute(text(_FILL_SQL[table].format(c=col)), {"y": year}).one()
            out[f"{table}.{col}"] = {
                "n": int(n),
                "filled": int(filled),
                "rate": (int(filled) / int(n)) if n else None,
            }
        conn.rollback()
    return out


#: key sets compared between the two DBs (codex D23 Q2 #3: counts cancel out; a missing race
#: hidden behind a surplus one must still block). Entries / results are keyed by 馬番, not
#: horse_id (the current DB may hold ``nk:`` surrogates for the same horse).
_KEY_SQL = {
    "races": "SELECT race_id FROM races WHERE extract(year FROM race_date) = :y",
    "entries": """SELECT rh.race_id || ':' || rh.horse_number FROM race_horses rh
                  JOIN races r USING (race_id)
                  WHERE extract(year FROM r.race_date) = :y AND rh.horse_number IS NOT NULL""",
    "results": """SELECT rr.race_id || ':' || rh.horse_number FROM race_results rr
                  JOIN race_horses rh USING (race_id, horse_id) JOIN races r USING (race_id)
                  WHERE extract(year FROM r.race_date) = :y AND rh.horse_number IS NOT NULL""",
    "payout_rows": """SELECT p.race_id || ':' || p.horse_number FROM official_win_payouts p
                      JOIN races r USING (race_id) WHERE extract(year FROM r.race_date) = :y""",
    "laps": """SELECT l.race_id FROM race_laps l JOIN races r USING (race_id)
               WHERE extract(year FROM r.race_date) = :y""",
}


def _keys(engine: Engine, year: int) -> dict[str, set[str]]:
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        out = {
            k: {str(x) for (x,) in conn.execute(text(sql), {"y": year})}
            for k, sql in _KEY_SQL.items()
        }
        conn.rollback()
    return out


def compare_year(target: Engine, reference: Engine, year: int) -> YearReport:
    """Counts (reported) + key sets (decisive): a key the current DB has and JRDB lacks blocks,
    for every compared set — payouts and laps included where the current DB has them (codex D23
    Q2 #4: "the old years have no payouts" is a reason for an EMPTY set, not for skipping it)."""

    rep = YearReport(year)
    rep.compared_at = datetime.datetime.now(datetime.UTC).isoformat()
    rep.counts_jrdb = _counts(target, year)
    rep.counts_current = _counts(reference, year)
    rep.fill_jrdb = _fill(target, year)
    kj, kc = _keys(target, year), _keys(reference, year)
    for k in _KEY_SQL:
        missing = sorted(kc[k] - kj[k])
        surplus = len(kj[k] - kc[k])
        rep.missing_in_jrdb[k] = {
            "n": len(missing),
            "sample": missing[:20],
            "surplus_in_jrdb": surplus,
        }
    rep.blocking = [k for k in _KEY_SQL if rep.missing_in_jrdb[k]["n"]]
    return rep


# --------------------------------------------------------------------------- the range


@dataclass
class RebuildReport:
    date_from: datetime.date
    date_to: datetime.date
    schema_head: str | None = None
    years: list[YearReport] = field(default_factory=list)
    blocking_years: list[int] = field(default_factory=list)
    report_paths: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    person_map_size: int = 0
    exit_code: int = 0
    error: str | None = None

    def to_json(self) -> dict:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "schema_head": self.schema_head,
            "years": [y.to_json() for y in self.years],
            "blocking_years": self.blocking_years,
            "report_paths": self.report_paths,
            "skipped_files": self.skipped_files,
            "person_map_size": self.person_map_size,
            "exit_code": self.exit_code,
            "error": self.error,
        }


def rebuild_range(
    files_dir: Path,
    date_from: datetime.date,
    date_to: datetime.date,
    *,
    db_url: str,
    reference_db_url: str,
    out_dir: Path,
    env_url: str | None = None,
    observed_at: datetime.datetime | None = None,
) -> RebuildReport:
    rep = RebuildReport(date_from, date_to)
    try:
        assert_distinct_targets(
            db_url,
            reference_db_url,
            env_url if env_url is not None else os.environ.get("DATABASE_URL"),
        )
    except JrdbStartupRefused as exc:
        rep.exit_code, rep.error = 2, f"{exc.code}: {exc}"
        return rep
    target, reference = create_db_engine(db_url), create_db_engine(reference_db_url)
    try:
        try:
            assert_distinct_databases(
                target,
                reference,
                env_url if env_url is not None else os.environ.get("DATABASE_URL"),
            )
            rep.schema_head = assert_same_schema(target, reference)
            assert_target_is_jrdb_only(target)
        except JrdbStartupRefused as exc:
            rep.exit_code, rep.error = 2, f"{exc.code}: {exc}"
            return rep
        by_day, rep.skipped_files = load_archive_days(files_dir, date_from, date_to)
        now = observed_at or datetime.datetime.now(datetime.UTC)
        factory = sessionmaker(bind=target, expire_on_commit=False)
        with factory() as session:
            person_map = W.load_person_map(session)
            rep.person_map_size = len(person_map)
        year_reports: dict[int, YearReport] = {}
        for day in sorted(by_day):
            yr = year_reports.setdefault(day.year, YearReport(day.year))
            yr.days += 1
            for step, kinds in STEPS:
                files = [f for f in by_day[day] if f.kind in kinds]
                if not files:
                    continue
                with factory() as session:
                    day_files = _day_files(session, files, kinds)
                    for f in day_files:
                        if f.error:
                            yr.notes.append(f.error)
                    session.commit()
                    yr.jobs.append(_run_step(session, step, kinds, day_files, day, now, person_map))
                    session.commit()
        out_dir.mkdir(parents=True, exist_ok=True)
        for year in range(date_from.year, date_to.year + 1):
            cmp = compare_year(target, reference, year)
            yr = year_reports.get(year)
            if yr is not None:
                cmp.days, cmp.jobs, cmp.notes = yr.days, yr.jobs, yr.notes
            rep.years.append(cmp)
            if cmp.blocking:
                rep.blocking_years.append(year)
            path = out_dir / f"compare-{year}.json"
            path.write_text(
                json.dumps(cmp.to_json(), ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            rep.report_paths.append(str(path))
        if rep.blocking_years:
            rep.exit_code = 1
            rep.error = (
                f"JRDB below the current DB in {rep.blocking_years} (FR-020: find the reason first)"
            )
    finally:
        target.dispose()
        reference.dispose()
    return rep


__all__ = [
    "BLOCKING_COUNTS",
    "STEPS",
    "ArchiveFile",
    "RebuildReport",
    "YearReport",
    "alembic_head",
    "assert_distinct_databases",
    "assert_distinct_targets",
    "assert_same_schema",
    "assert_target_is_jrdb_only",
    "db_identity",
    "compare_year",
    "load_archive_days",
    "rebuild_range",
]
