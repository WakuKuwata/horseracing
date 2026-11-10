"""``scrape jrdb-entries / jrdb-prerace / jrdb-results / jrdb-payouts / jrdb-audit`` (Feature 178,
T051/T052; contracts/cli.md). The US2 writer commands.

Flow per command: startup checks (policy ``jrdb_enabled``; no credentials — the files are already
on disk) → the day's files from ``artifacts/jrdb/<KIND>/<yyyymmdd>/`` → the ``jrdb_files`` row of
each (version verdict; ``held`` files are reported and never written) → decode under the given
profile → the writer (one savepoint per race) → ``race_status`` / ``status`` on the file row →
``ingestion_jobs`` summary (``files`` = the published_at material of data-model §3a).

``--profile`` has no default: the operator spells ``current`` on the production DB;
``jrdb_canonical``
refuses to run against the production ``DATABASE_URL`` (codex D20 #9).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from horseracing_db.models.jrdb import JrdbFile
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..pipeline import _run_job
from ..upsert import Counts
from . import files as jfiles
from . import upsert_jrdb as W
from .decode import DecodedFile, decode_file
from .errors import JrdbError, JrdbStartupRefused
from .policy import DEFAULT_POLICY_PATH, assert_profile_binding, read_policy, require_enabled
from .profile import profile_from_arg

SOURCE = "jrdb"
WRITE_COMMANDS = ("jrdb-entries", "jrdb-prerace", "jrdb-results", "jrdb-payouts", "jrdb-audit")
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ARTIFACTS = _REPO_ROOT / "artifacts" / "jrdb"
KINDS_FOR = {
    "jrdb-entries": ("BAC", "KYI", "UKC"),
    "jrdb-prerace": ("TYB",),
    "jrdb-results": ("SED", "SRB"),
    "jrdb-payouts": ("HJC",),
}


def register_write_subcommands(sub: argparse._SubParsersAction) -> None:
    for name in WRITE_COMMANDS:
        p = sub.add_parser(
            name,
            help=f"{name}: JRDB → 製品表 (178 US2)"
            if name != "jrdb-audit"
            else "jrdb-audit: 来歴・jrdb_files・artifacts の整合報告 (READ ONLY)",
        )
        p.add_argument("--date", required=True, help="開催日 YYYY-MM-DD (ファイル名の日付)")
        if name != "jrdb-audit":
            p.add_argument(
                "--profile",
                required=True,
                choices=["current", "jrdb_canonical"],
                help="書込み先プロファイル (既定なし)",
            )
        if name == "jrdb-prerace":
            p.add_argument(
                "--venue",
                default=None,
                choices=["E", "W", "L"],
                help="場別上書きファイル TYB_E/W/L",
            )
        p.add_argument("--database-url", default=None)
        p.add_argument(
            "--policy", default=None, help=f"policy file (default {DEFAULT_POLICY_PATH})"
        )
        p.add_argument(
            "--artifacts-dir", default=None, help=f"展開済みテキスト (default {DEFAULT_ARTIFACTS})"
        )
        p.add_argument("--json", action="store_true")


@dataclass
class WriteResult:
    command: str
    date: str
    jobs: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    audit: dict | None = None
    exit_code: int = 0
    error: str | None = None

    def to_json(self) -> dict:
        return {
            "command": self.command,
            "date": self.date,
            "jobs": self.jobs,
            "notes": self.notes,
            "audit": self.audit,
            "exit_code": self.exit_code,
            "error": self.error,
        }


# --------------------------------------------------------------------------- files of the day


@dataclass(frozen=True)
class DayFile:
    path: Path
    meta: object  # JrdbFileMeta
    row: JrdbFile | None
    decoded: DecodedFile | None
    error: str | None


def _day_files(
    session: Session, artifacts: Path, kinds, day: datetime.date, *, profile: str, venue: str | None
) -> list[DayFile]:
    out: list[DayFile] = []
    for kind in kinds:
        folder = artifacts / kind / day.strftime("%Y%m%d")
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.txt")):
            if (
                kind == "TYB"
                and venue is not None
                and not path.name.upper().startswith(f"TYB_{venue}")
            ):
                continue
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            row = session.scalar(
                select(JrdbFile).where(JrdbFile.sha256 == sha, JrdbFile.kind == kind)
            )
            variant = row.variant if row is not None else "daily"
            # ``save_extracted`` keeps a second version of the same name as
            # ``KYI260913.<sha12>.txt``: the file's identity is the plain name (T073)
            plain_name = path.name.split(".")[0] + ".txt"
            try:
                meta = jfiles.build_meta(
                    plain_name,
                    raw,
                    variant=variant,
                    fetched_at=row.fetched_at if row else datetime.datetime.now(datetime.UTC),
                    data_date=day,
                )
                decoded = decode_file(raw, meta, profile=profile)
                out.append(DayFile(path, meta, row, decoded, None))
            except JrdbError as exc:
                out.append(
                    DayFile(path, None, row, None, f"{path.name}: {type(exc).__name__}: {exc}")
                )
    return out


def _files_summary(files: list[DayFile]) -> list[dict]:
    return [
        {
            "jrdb_file_id": str(f.row.jrdb_file_id) if f.row else None,
            "kind": f.meta.kind if f.meta else None,
            "data_date": f.meta.data_date.isoformat() if f.meta else None,
            "variant": f.meta.variant if f.meta else None,
            "sha256": f.meta.sha256 if f.meta else None,
            "published_at": f.row.published_at.isoformat()
            if f.row and f.row.published_at
            else None,
            "fetched_at": f.row.fetched_at.isoformat() if f.row and f.row.fetched_at else None,
            "fetched_at_basis": "http_fetch" if f.row and f.row.fetched_at else None,
            "rows_rejected": len(f.decoded.rejected_rows) if f.decoded else None,
        }
        for f in files
    ]


def _apply_status(session: Session, f: DayFile, status: W.RaceStatus, job_id, now) -> None:
    if f.row is None:
        return
    merged = W.merge_race_status(f.row.race_status, status)
    f.row.race_status = merged
    all_status = {v["status"] for v in merged.values()}
    if merged and all_status <= {"applied", "skipped"} and "applied" in all_status:
        f.row.status = "applied"
        f.row.applied_at = now
    elif any(v["status"] == "applied" for v in merged.values()):
        f.row.status = "partially_applied"
        f.row.applied_at = f.row.applied_at or now
    f.row.ingestion_job_id = job_id
    session.flush()


# --------------------------------------------------------------------------- the writer commands


def _rows(files: list[DayFile], kind: str) -> list:
    out = []
    for f in files:
        if f.decoded is not None and f.decoded.kind == kind:
            out.extend(f.decoded.rows)
    return out


def run_write(args, *, session_factory, artifacts: Path, production_url: str | None) -> WriteResult:
    day = datetime.date.fromisoformat(args.date)
    result = WriteResult(args.command, day.isoformat())
    policy_path = Path(args.policy) if args.policy else DEFAULT_POLICY_PATH
    try:
        require_enabled(read_policy(policy_path))
        assert_profile_binding(args.profile, args.database_url, production_url)
    except JrdbStartupRefused as exc:
        result.exit_code, result.error = 2, f"{exc.code}: {exc}"
        return result
    profile = profile_from_arg(args.profile)
    kinds = KINDS_FOR[args.command]
    now = datetime.datetime.now(datetime.UTC)
    with session_factory() as session:
        files = _day_files(
            session, artifacts, kinds, day, profile=profile.name, venue=getattr(args, "venue", None)
        )
        for f in files:
            if f.error:
                result.notes.append(f.error)
        held = [f for f in files if f.row is not None and f.row.status == "held"]
        for f in held:
            result.notes.append(f"held: {f.path.name} (operator approval needed; not written)")
        usable = [
            f
            for f in files
            if f.decoded is not None
            and (f.row is None or f.row.status not in ("held", "rejected", "superseded"))
        ]
        if not usable:
            result.notes.append(f"no {'/'.join(kinds)} file for {day} under {artifacts}")
            result.exit_code = 1
            return result
        person_map = W.load_person_map(session)
        statuses: dict[int, W.RaceStatus] = {id(f): W.RaceStatus() for f in usable}

        def work(job_id) -> Counts:
            c = Counts()
            c.extra["files"] = _files_summary(usable)  # type: ignore[assignment]
            c.extra["rows_read"] = sum(f.decoded.n_rows for f in usable)
            c.extra["rows_rejected"] = sum(len(f.decoded.rejected_rows) for f in usable)
            c.extra["held"] = len(held)
            if args.command == "jrdb-entries":
                kyi = [f for f in usable if f.decoded.kind == "KYI"]
                for f in kyi:
                    already = f.row.race_status if f.row else None
                    rids = {W.race_id_from_key(e.key, file_year=day.year) for e in f.decoded.rows}
                    todo = W.unapplied_races(already, sorted(r for r in rids if r))
                    c.merge(
                        W.upsert_jrdb_entries(
                            session,
                            _rows(usable, "BAC"),
                            f.decoded.rows,
                            _rows(usable, "UKC"),
                            profile=profile,
                            year=day.year,
                            observed_at=now,
                            job_id=job_id,
                            person_map=person_map,
                            race_status=statuses[id(f)],
                            only_races=todo,
                        )
                    )
            elif args.command == "jrdb-prerace":
                for f in usable:
                    c.merge(
                        W.upsert_jrdb_prerace(
                            session,
                            f.decoded.rows,
                            profile=profile,
                            year=day.year,
                            observed_at=now,
                            job_id=job_id,
                            race_status=statuses[id(f)],
                        )
                    )
            elif args.command == "jrdb-results":
                for f in [x for x in usable if x.decoded.kind == "SED"]:
                    c.merge(
                        W.upsert_jrdb_results(
                            session,
                            f.decoded.rows,
                            _rows(usable, "SRB"),
                            profile=profile,
                            edition=f.meta.edition,
                            year=day.year,
                            observed_at=now,
                            job_id=job_id,
                            race_status=statuses[id(f)],
                            person_map=person_map,
                        )
                    )
            elif args.command == "jrdb-payouts":
                for f in usable:
                    c.merge(
                        W.upsert_jrdb_payouts(
                            session,
                            f.decoded.rows,
                            profile=profile,
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
            job_type=args.command.replace("-", "_"),
            scope="date",
            scope_value=day.isoformat(),
            work=work,
            source=SOURCE,
            pass_job_id=True,
        )
        result.jobs.append(
            {
                "job_type": summary.job_type,
                "status": summary.status,
                "processed": summary.processed,
                "written": summary.written,
                "skipped": summary.skipped,
                "errors": summary.errors,
                **{k: v for k, v in (summary.extra or {}).items() if k != "files"},
            }
        )
        if summary.status == "failed":
            result.exit_code = 1
    return result


# --------------------------------------------------------------------------- jrdb-audit (READ ONLY)


_AUDIT_SQL = {
    "provenance_horse": """
        SELECT p.field_group, p.source, count(*)
        FROM race_horse_field_provenance p JOIN races r USING (race_id)
        WHERE r.race_date = :day GROUP BY 1, 2 ORDER BY 1, 2""",
    "provenance_race": """
        SELECT p.field_group, p.source, count(*)
        FROM race_field_provenance p JOIN races r USING (race_id)
        WHERE r.race_date = :day GROUP BY 1, 2 ORDER BY 1, 2""",
    "nk_rows": """
        SELECT count(*) FROM race_horses rh JOIN races r USING (race_id)
        WHERE r.race_date = :day AND rh.horse_id LIKE 'nk:%'""",
    "unresolved_person": """
        SELECT count(*) FROM race_horses rh JOIN races r USING (race_id)
        JOIN race_horse_field_provenance p
          ON p.race_id = rh.race_id AND p.horse_id = rh.horse_id AND p.field_group = 'entry_static'
        WHERE r.race_date = :day AND p.source = 'jrdb'
          AND (rh.jockey_id IS NULL OR rh.trainer_id IS NULL)""",
}


def run_audit(args, *, session_factory, artifacts: Path) -> WriteResult:
    day = datetime.date.fromisoformat(args.date)
    result = WriteResult("jrdb-audit", day.isoformat())
    audit: dict = {
        "day": day.isoformat(),
        "files": [],
        "inv_f3_sha_mismatch": [],
        "inv_f4_status_mismatch": [],
    }
    with session_factory() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            for key, sql in _AUDIT_SQL.items():
                rows = session.execute(text(sql), {"day": day}).all()
                audit[key] = (
                    [list(r) for r in rows]
                    if len(rows[0]) > 1
                    else int(rows[0][0])
                    if rows
                    else None
                )
            files = session.scalars(select(JrdbFile).where(JrdbFile.data_date == day)).all()
            for f in files:
                entry = {
                    "jrdb_file_id": str(f.jrdb_file_id),
                    "kind": f.kind,
                    "variant": f.variant,
                    "status": f.status,
                    "sha256": f.sha256,
                    "races": len(f.race_status or {}),
                }
                audit["files"].append(entry)
                # INV-F4: applied ⇔ every race applied
                st = {v.get("status") for v in (f.race_status or {}).values()}
                if f.status == "applied" and (not st or st - {"applied", "skipped"}):
                    audit["inv_f4_status_mismatch"].append(entry["jrdb_file_id"])
                # INV-F3: an applied file's text is on disk with the same sha
                if f.status in ("applied", "partially_applied"):
                    folder = artifacts / f.kind / day.strftime("%Y%m%d")
                    found = (
                        any(
                            hashlib.sha256(p.read_bytes()).hexdigest() == f.sha256
                            for p in folder.glob("*.txt")
                        )
                        if folder.is_dir()
                        else False
                    )
                    if not found:
                        audit["inv_f3_sha_mismatch"].append(entry["jrdb_file_id"])
            audit["stage"] = stage_status(
                session, day, Path(args.policy) if getattr(args, "policy", None) else None
            )
            persons = persons_readiness(session, artifacts, day)
            audit["stage"]["persons"] = persons
            if persons is not None and not persons["ready"]:
                n = len(persons["jockey"]["unreviewed"]) + len(persons["trainer"]["unreviewed"])
                audit["stage"]["stopped"].insert(
                    0, f"persons: {n} jockey/trainer code(s) never reviewed (run jrdb-persons)"
                )
        finally:
            session.rollback()
    result.audit = audit
    return result


# --------------------------------------------------------------------------- 段の状態 (T070)

_STAGE_SQL = {
    "races": "SELECT count(*), count(post_time) FROM races WHERE race_date = :day",
    "entries_by_source": """
        SELECT p.source, count(DISTINCT p.race_id) FROM race_horse_field_provenance p
        JOIN races r USING (race_id)
        WHERE r.race_date = :day AND p.field_group = 'entry_static' GROUP BY 1 ORDER BY 1""",
    "quotes_by_source": """
        SELECT q.source, count(*) FROM decision_quotes q JOIN races r USING (race_id)
        WHERE r.race_date = :day GROUP BY 1 ORDER BY 1""",
    "results_races": """
        SELECT count(DISTINCT rr.race_id) FROM race_results rr JOIN races r USING (race_id)
        WHERE r.race_date = :day""",
    "final_odds_races": """
        SELECT count(*) FROM races r WHERE r.race_date = :day AND r.win_odds_official_at IS NULL
          AND EXISTS (SELECT 1 FROM race_horses rh WHERE rh.race_id = r.race_id
                      AND rh.entry_status = 'started')
          AND NOT EXISTS (SELECT 1 FROM race_horses rh WHERE rh.race_id = r.race_id
                          AND rh.entry_status = 'started' AND rh.odds IS NULL)""",
    "payout_races": """
        SELECT count(DISTINCT p.race_id) FROM official_win_payouts p JOIN races r USING (race_id)
        WHERE r.race_date = :day""",
}
#: what a race day needs from JRDB, in arrival order (前日 → 当日 → 夜 → 木曜)
STAGE_KINDS = ("BAC", "KYI", "UKC", "TYB", "HJC", "SED", "SRB")


def stage_status(session: Session, day: datetime.date, policy_path: Path | None) -> dict:
    """US4 受入 2: one screen that says which stage is stopped — the day's JRDB files and their
    status, which route created the entry rows, how many T−3 records exist and from which source,
    results / final odds / payouts coverage, and the policy switches as they stand now."""

    from ..netkeiba_policy import (
        EMERGENCY_KEY,
        JRDB_KEY,
        NETKEIBA_KEY,
        PolicyFlagInvalid,
        policy_fingerprint,
        read_flag,
        read_policy_doc,
    )

    files: dict[str, list[str]] = {k: [] for k in STAGE_KINDS}
    expected_races: int | None = None  # the KYI file's race set, when it has been seen
    for row in session.scalars(select(JrdbFile).where(JrdbFile.data_date == day)).all():
        files.setdefault(row.kind, []).append(f"{row.variant}:{row.status}")
        if row.kind == "KYI" and row.race_status:
            expected_races = max(expected_races or 0, len(row.race_status))
    n_races, n_post = session.execute(text(_STAGE_SQL["races"]), {"day": day}).one()
    out: dict = {
        "day": day.isoformat(),
        "jrdb_files": files,
        "jrdb_files_missing": [k for k in STAGE_KINDS if not files.get(k)],
        "expected_races": expected_races,
        "races": int(n_races),
        "races_with_post_time": int(n_post),
        "entries_by_source": {
            s: int(n)
            for s, n in session.execute(text(_STAGE_SQL["entries_by_source"]), {"day": day})
        },
        "decision_quotes_by_source": {
            s: int(n)
            for s, n in session.execute(text(_STAGE_SQL["quotes_by_source"]), {"day": day})
        },
        "results_races": int(
            session.execute(text(_STAGE_SQL["results_races"]), {"day": day}).scalar_one()
        ),
        "final_odds_races": int(
            session.execute(text(_STAGE_SQL["final_odds_races"]), {"day": day}).scalar_one()
        ),
        "payout_races": int(
            session.execute(text(_STAGE_SQL["payout_races"]), {"day": day}).scalar_one()
        ),
    }
    try:
        doc = read_policy_doc(policy_path)
        out["policy"] = {
            NETKEIBA_KEY: read_flag(doc, NETKEIBA_KEY, True),
            JRDB_KEY: read_flag(doc, JRDB_KEY, False),
            EMERGENCY_KEY: read_flag(doc, EMERGENCY_KEY, False),
            "near_post_source": doc.get("near_post_source", "netkeiba"),
            **policy_fingerprint(policy_path),  # which file, which version (codex D25 Q1-4)
        }
    except (PolicyFlagInvalid, ValueError) as exc:
        out["policy"] = {"error": f"{type(exc).__name__}: {exc}"}
    stopped = []
    if out["races"] == 0 and expected_races is None:
        # codex D25 Q2-1: 0 rows and no JRDB 番組 — a 休催, an unfetched day or a stopped stage
        # cannot be told apart from the DB alone
        stopped.append(
            "undetermined: no race rows and no KYI file for the day (休催か未取得か不明)"
        )
    elif out["races"] == 0:
        stopped.append("entries: no race rows for the day (JRDB 前日 file or emergency mode)")
    elif expected_races is not None and out["races"] < expected_races:
        stopped.append(f"entries: {expected_races - out['races']} KYI race(s) without rows")
    elif out["races_with_post_time"] < out["races"]:
        stopped.append("entries: races without post_time (the T−3 schedule skips them)")
    if out["races"] and not out["decision_quotes_by_source"]:
        stopped.append("near_post: no T−3 record yet")
    if out["races"] and out["results_races"] < out["races"]:
        stopped.append("results: not every race has results")
    if out["races"] and out["final_odds_races"] < out["races"]:
        stopped.append("final_odds: not every race has quote-less odds for every starter")
    if out["races"] and out["payout_races"] < out["races"]:
        stopped.append("payouts: not every race has 単勝 payouts")
    out["stopped"] = stopped
    return out


def persons_readiness(session: Session, artifacts: Path, day: datetime.date) -> dict | None:
    """R5 gate (2026-11-09): the jockey/trainer codes of the day's KYI against
    ``id_mappings(source=jrdb)``. ``mapped`` = an official id is written; ``reviewed_gap`` = the
    code was reviewed (unmapped/conflict row exists — e.g. a 地方/外国人 with no official id in the
    DB, the row stays NULL by decision); ``unreviewed`` = no row at all (a new person since the
    last ``jrdb-persons`` run). Only ``unreviewed`` blocks the day. ``None`` when no KYI text."""

    from horseracing_db.enums import MappingStatus
    from horseracing_db.models import IdMapping

    files = _day_files(session, artifacts, ("KYI",), day, profile="current", venue=None)
    kyi = [
        f
        for f in files
        if f.decoded is not None and (f.row is None or f.row.status != "superseded")
    ]
    if not kyi:
        return None
    codes: dict[str, set[str]] = {"jockey": set(), "trainer": set()}
    for f in kyi:
        for e in f.decoded.rows:
            if e.jockey_code:
                codes["jockey"].add(e.jockey_code)
            if e.trainer_code:
                codes["trainer"].add(e.trainer_code)
    out: dict = {}
    for kind, cs in codes.items():
        rows = {
            r.source_id: r.mapping_status
            for r in session.scalars(
                select(IdMapping).where(
                    IdMapping.entity_type == kind,
                    IdMapping.source == SOURCE,
                    IdMapping.source_id.in_(sorted(cs)),
                )
            )
        }
        mapped = sorted(c for c in cs if rows.get(c) == MappingStatus.MAPPED)
        unreviewed = sorted(c for c in cs if c not in rows)
        gap = sorted(c for c in cs if c in rows and c not in mapped)
        out[kind] = {
            "codes": len(cs),
            "mapped": len(mapped),
            "reviewed_gap": gap,
            "unreviewed": unreviewed,
        }
    out["ready"] = not (out["jockey"]["unreviewed"] or out["trainer"]["unreviewed"])
    return out


def run_write_command(args) -> int:
    from horseracing_db.session import create_db_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_db_engine(args.database_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifacts = Path(args.artifacts_dir) if args.artifacts_dir else DEFAULT_ARTIFACTS
    if args.command == "jrdb-audit":
        result = run_audit(args, session_factory=session_factory, artifacts=artifacts)
    else:
        result = run_write(
            args,
            session_factory=session_factory,
            artifacts=artifacts,
            production_url=os.environ.get("DATABASE_URL"),
        )
    print(
        json.dumps(
            result.to_json(), ensure_ascii=False, indent=None if args.json else 2, default=str
        )
    )
    return result.exit_code


__all__ = [
    "KINDS_FOR",
    "WRITE_COMMANDS",
    "WriteResult",
    "register_write_subcommands",
    "run_audit",
    "run_write",
    "run_write_command",
    "persons_readiness",
]
