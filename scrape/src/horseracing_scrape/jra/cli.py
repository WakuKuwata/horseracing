"""`scrape jra-*` サブコマンド(Feature 176, contracts/cli.md).

共通の流れ: 起動時検証(``policy.validate_startup``・1 件も送る前)→ ``JraFetcher``(共有スロット・
捕捉時間帯の guard)→ ``navigate.Walker`` → 解析 → ``pipeline._run_job(source=jra_official)`` →
summary JSON → exit code(0=SUCCEEDED/SKIPPED, 1=PARTIAL, 2=FAILED)。

``--dry-run`` は取得と解析まで(共有スロットの予約行だけ動く): ``_run_job`` も writer も呼ばない。

書込み層(``upsert_jra``)と取得層は注入可能なので、テストは fixture を返す fetcher と一時 DB の
session factory で同じコードを通す。
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from horseracing_db.enums import JobStatus, Source
from sqlalchemy.orm import Session, sessionmaker

from .. import robots_cache
from ..capture_window import active_hold
from ..fetch import FetchRefused
from ..models import ParseError
from ..pipeline import JobSummary, _run_job
from ..upsert import Counts
from .errors import JraParameterError
from .fetch import CaptureWindowRefused, JraFetcher
from .models import KIND_CHANGES, KIND_ENTRIES, KIND_ODDS_WIN
from .navigate import CHANGES_CNAME, CHANGES_URL, Walker
from .parse.changes import parse_changes
from .parse.entries import parse_entries_day
from .parse.odds import parse_win_odds
from .policy import DEFAULT_POLICY_PATH, StartupRefused, validate_startup

JST = datetime.timezone(datetime.timedelta(hours=9))
JRA = Source.JRA_OFFICIAL
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ARCHIVE_DIR = _REPO_ROOT / "artifacts" / "jra_archive"
COMMANDS = (
    "jra-entries",
    "jra-odds",
    "jra-results",
    "jra-payouts",
    "jra-baba",
    "jra-fetch",
    "jra-audit",
)


# --- argparse ---------------------------------------------------------------------------------
def register_jra_subcommands(sub: argparse._SubParsersAction) -> None:
    for name in COMMANDS:
        p = sub.add_parser(name, help=f"{name}: JRA 公式サイト(176)")
        p.add_argument("--date", default=None, help="開催日 YYYY-MM-DD(JST・既定=今日)")
        p.add_argument("--venue", default=None, help="競馬場コード 2 桁で 1 場に限る(任意)")
        p.add_argument(
            "--race-id",
            action="append",
            default=None,
            dest="race_ids",
            help="この race_id のブロックだけ書く(ページは丸ごと取る・repeat)",
        )
        p.add_argument(
            "--dry-run", action="store_true", help="取得と解析まで。業務表・job・来歴は書かない"
        )
        p.add_argument("--json", action="store_true", help="summary を JSON で標準出力")
        p.add_argument("--database-url", default=None)
        p.add_argument(
            "--policy", default=None, help=f"policy file (default {DEFAULT_POLICY_PATH})"
        )
        p.add_argument(
            "--archive-dir", default=None, help=f"raw archive (default {DEFAULT_ARCHIVE_DIR})"
        )
        if name == "jra-fetch":
            p.add_argument("--save-dir", required=True)
            p.add_argument(
                "--kinds",
                default="entries,changes,results,payouts,baba",
                help="comma separated: entries,changes,odds,results,payouts,baba "
                "(odds は取得・解析のみ・raw は保存しない)",
            )
        if name == "jra-audit":
            p.add_argument("--from-dir", required=True)


# --- run context ------------------------------------------------------------------------------
@dataclass
class RunResult:
    command: str
    date: datetime.date
    jobs: list[JobSummary] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)
    mismatches: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dry_run: bool = False
    stopped_for_capture_window: int = 0
    unsupported_date: int = 0
    requests_sent: int = 0
    exit_code: int = 0
    #: jra-audit only: the AuditReport as JSON
    audit: dict | None = None

    def to_json(self) -> dict:
        doc = {
            "command": self.command,
            "date": self.date.isoformat(),
            "dry_run": self.dry_run,
            "requests_sent": self.requests_sent,
            "stopped_for_capture_window": self.stopped_for_capture_window,
            "unsupported_date": self.unsupported_date,
            "jobs": [
                {
                    "job_type": j.job_type,
                    "status": j.status,
                    "processed": j.processed,
                    "written": j.written,
                    "skipped": j.skipped,
                    "errors": j.errors,
                    "extra": dict(j.extra),
                }
                for j in self.jobs
            ],
            "quarantined": self.quarantined,
            "mismatches": self.mismatches,
            "notes": self.notes,
            "exit_code": self.exit_code,
        }
        if self.audit is not None:
            doc["audit"] = self.audit
        return doc


def _parse_date(raw: str | None) -> datetime.date:
    if raw is None:
        return datetime.datetime.now(JST).date()
    return datetime.date.fromisoformat(raw)


def _collect(c: Counts, result: RunResult) -> None:
    for m in c.error_messages:
        if m.startswith("quarantined"):
            result.quarantined.append(m)
        elif "mismatch" in m:
            result.mismatches.append(m)
        else:
            result.notes.append(m)


def _finish(result: RunResult) -> RunResult:
    if result.exit_code == 2:  # set by the command itself (startup/manifest failure)
        return result
    statuses = {j.status for j in result.jobs}
    if JobStatus.FAILED in statuses:
        result.exit_code = 2
    elif JobStatus.PARTIAL in statuses or result.stopped_for_capture_window:
        result.exit_code = 1
    else:
        result.exit_code = 0
    return result


# --- commands --------------------------------------------------------------------------------
def cmd_entries(
    args, *, fetcher, session_factory: Callable[[], Session], date: datetime.date
) -> RunResult:
    result = RunResult("jra-entries", date, dry_run=bool(args.dry_run))
    walker = Walker(fetcher, date=date)
    pages = walker.day_pages("entries", venue=args.venue)
    if not pages:
        result.unsupported_date = 1
        result.notes.append(f"unsupported_date: {date} is not on the index")
        if not args.dry_run:
            with session_factory() as session:
                for jt in ("jra_entries", "jra_changes"):
                    result.jobs.append(
                        _run_job(
                            session,
                            job_type=jt,
                            scope="date",
                            scope_value=date.isoformat(),
                            source=JRA,
                            work=lambda: Counts(
                                skipped=1,
                                extra={
                                    "unsupported_date": 1,
                                    "requests_sent": fetcher.requests_sent,
                                },
                            ),
                        )
                    )
        result.requests_sent = fetcher.requests_sent
        return _finish(result)
    changes_resp = fetcher.post(CHANGES_URL, CHANGES_CNAME, kind=KIND_CHANGES)
    try:
        changes = parse_changes(changes_resp.text, expected_date=date)
        changes_error = None
    except ParseError as exc:
        changes, changes_error = (), str(exc)
    parsed = []
    page_errors: list[str] = []
    for day, resp in pages:
        try:
            parsed.append((resp, parse_entries_day(resp.text, expected_date=date)))
        except ParseError as exc:
            page_errors.append(f"entries page {day.venue_code}: {exc}")
    result.requests_sent = fetcher.requests_sent
    if args.dry_run:
        for _, page in parsed:
            result.notes.append(f"{page.venue_code}: {page.state_counts()}")
        result.notes.extend(page_errors)
        result.notes.append(
            f"changes parsed: {len(changes)}" + (f" ({changes_error})" if changes_error else "")
        )
        return _finish(result)

    from .emergency_entries import create_entries_from_official, emergency_enabled
    from .upsert_jra import apply_jra_changes, upsert_jra_entries

    # Feature 178 (T050a): policy-only switch, read once per call; False = the 176 path unchanged
    emergency = emergency_enabled(Path(args.policy) if args.policy else DEFAULT_POLICY_PATH)

    with session_factory() as session:

        def work_entries(job_id) -> Counts:
            agg = Counts(
                extra={"requests_sent": fetcher.requests_sent, "rewalks": walker.report.rewalks}
            )
            for resp, page in parsed:
                for k, v in page.state_counts().items():
                    agg.bump(k, v)
                if emergency:
                    agg.merge(
                        create_entries_from_official(
                            session,
                            page,
                            changes,
                            policy=True,
                            observed_at=resp.fetched_at,
                            job_id=job_id,
                            race_ids=args.race_ids,
                        )
                    )
                agg.merge(
                    upsert_jra_entries(
                        session,
                        page,
                        changes,
                        observed_at=resp.fetched_at,
                        job_id=job_id,
                        race_ids=args.race_ids,
                    )
                )
            for e in page_errors:
                agg.errors += 1
                agg.error_messages.append(e)
            return agg

        j1 = _run_job(
            session,
            job_type="jra_entries",
            scope="date",
            scope_value=date.isoformat(),
            source=JRA,
            work=work_entries,
            pass_job_id=True,
        )
        result.jobs.append(j1)

        def work_changes(job_id) -> Counts:
            agg = Counts(extra={"requests_sent": 0, "shared_fetch_with_jra_entries": 1})
            if changes_error:
                agg.errors += 1
                agg.error_messages.append(f"changes page unreadable: {changes_error}")
                return agg
            agg.merge(
                apply_jra_changes(
                    session,
                    changes,
                    observed_at=changes_resp.fetched_at,
                    job_id=job_id,
                    race_ids=args.race_ids,
                )
            )
            return agg

        j2 = _run_job(
            session,
            job_type="jra_changes",
            scope="date",
            scope_value=date.isoformat(),
            source=JRA,
            work=work_changes,
            pass_job_id=True,
        )
        result.jobs.append(j2)
        _collect_summaries(session, result, (j1, j2))
    return _finish(result)


def _collect_summaries(session: Session, result: RunResult, jobs: Sequence[JobSummary]) -> None:
    """Pull the job rows' error_message lines into the quarantined / mismatches buckets."""
    from horseracing_db.models import IngestionJob
    from sqlalchemy import select

    for j in jobs:
        row = (
            session.execute(
                select(IngestionJob)
                .where(
                    IngestionJob.job_type == j.job_type, IngestionJob.scope_value == j.scope_value
                )
                .order_by(IngestionJob.created_at.desc())
            )
            .scalars()
            .first()
        )
        if row is None or not row.error_message:
            continue
        for line in row.error_message.splitlines():
            if line.startswith("quarantined"):
                result.quarantined.append(line)
            elif "mismatch" in line:
                result.mismatches.append(line)
            else:
                result.notes.append(line)


def _race_no_of_label(label: str) -> int | None:
    import re

    m = re.search(r"(\d{1,2})", label)
    return int(m.group(1)) if m else None


def cmd_odds(args, *, fetcher, session_factory, date: datetime.date) -> RunResult:
    result = RunResult("jra-odds", date, dry_run=bool(args.dry_run))
    walker = Walker(fetcher, date=date)
    wanted_nos = None
    if args.race_ids:
        wanted_nos = {(rid[4:6], int(rid[10:12])) for rid in args.race_ids}
    parsed = []
    errors: list[str] = []
    for day, label, resp in walker.odds_pages(venue=args.venue, want=wanted_nos):
        try:
            parsed.append((resp, parse_win_odds(resp.text, expected_date=date)))
        except ParseError as exc:
            errors.append(f"odds page {day.venue_code} {label}: {exc}")
    result.requests_sent = fetcher.requests_sent
    if walker.report.unsupported_date:
        result.unsupported_date = 1
    if args.dry_run:
        result.notes.extend(
            f"{o.race_id}: {o.kind} official_at={o.official_at} rows={len(o.rows)}"
            for _, o in parsed
        )
        result.notes.extend(errors)
        return _finish(result)

    from .upsert_jra import upsert_jra_odds

    with session_factory() as session:

        def work(job_id) -> Counts:
            agg = Counts(
                extra={
                    "requests_sent": fetcher.requests_sent,
                    "rewalks": walker.report.rewalks,
                    "unsupported_date": result.unsupported_date,
                }
            )
            for resp, odds in parsed:
                if args.race_ids and odds.race_id not in args.race_ids:
                    continue
                agg.merge(
                    upsert_jra_odds(session, odds, observed_at=resp.fetched_at, job_id=job_id)
                )
            for e in errors:
                agg.errors += 1
                agg.error_messages.append(e)
            if not parsed and result.unsupported_date:
                agg.skipped += 1
            return agg

        j = _run_job(
            session,
            job_type="jra_odds",
            scope="date",
            scope_value=date.isoformat(),
            source=JRA,
            work=work,
            pass_job_id=True,
        )
        result.jobs.append(j)
        _collect_summaries(session, result, (j,))
    return _finish(result)


def cmd_results(args, *, fetcher, session_factory, date: datetime.date) -> RunResult:
    from .results_cli import run_results

    return _finish(run_results(args, fetcher=fetcher, session_factory=session_factory, date=date))


def cmd_payouts(args, *, fetcher, session_factory, date: datetime.date) -> RunResult:
    from .results_cli import run_payouts

    return _finish(run_payouts(args, fetcher=fetcher, session_factory=session_factory, date=date))


def cmd_baba(args, *, fetcher, session_factory, date: datetime.date) -> RunResult:
    from .baba_cli import run_baba

    return _finish(run_baba(args, fetcher=fetcher, session_factory=session_factory, date=date))


def cmd_fetch(args, *, fetcher, session_factory, date: datetime.date) -> RunResult:
    from .audit import run_fetch

    return _finish(run_fetch(args, fetcher=fetcher, date=date))


def cmd_audit(args, *, fetcher, session_factory, date: datetime.date) -> RunResult:
    from .audit import run_audit

    return _finish(run_audit(args, session_factory=session_factory, date=date))


_DISPATCH = {
    "jra-entries": cmd_entries,
    "jra-odds": cmd_odds,
    "jra-results": cmd_results,
    "jra-payouts": cmd_payouts,
    "jra-baba": cmd_baba,
    "jra-fetch": cmd_fetch,
    "jra-audit": cmd_audit,
}


def run_command(command: str, args, *, fetcher, session_factory, date: datetime.date) -> RunResult:
    """Dispatch with the refusal/parameter-error classification of FR-005 applied uniformly."""
    try:
        return _DISPATCH[command](args, fetcher=fetcher, session_factory=session_factory, date=date)
    except CaptureWindowRefused as exc:
        r = RunResult(command, date, dry_run=bool(getattr(args, "dry_run", False)))
        r.stopped_for_capture_window = 1
        r.requests_sent = getattr(fetcher, "requests_sent", 0)
        r.notes.append(f"stopped for capture window: {exc}")
        return _finish(r)
    except FetchRefused as exc:
        r = RunResult(command, date, dry_run=bool(getattr(args, "dry_run", False)))
        r.requests_sent = getattr(fetcher, "requests_sent", 0)
        r.notes.append(f"refused: {exc} — run stopped; cooldown recorded")
        r.exit_code = 2
        return r
    except JraParameterError as exc:
        r = RunResult(command, date, dry_run=bool(getattr(args, "dry_run", False)))
        r.requests_sent = getattr(fetcher, "requests_sent", 0)
        r.notes.append(f"parameter error after re-walk: {exc}")
        r.exit_code = 2
        return r


# --- the real wiring (production) ---------------------------------------------------------------
def _netkeiba_interval_from_policy(policy_path: Path) -> float:
    try:
        doc = json.loads(policy_path.read_text(encoding="utf-8"))
        return float(doc.get("source_interval_seconds", 10.0))
    except (OSError, ValueError, TypeError):
        return 10.0


def run_jra(args) -> int:
    """Entry point from ``horseracing_scrape.cli``. Returns the exit code."""
    date = _parse_date(args.date)
    policy_path = Path(args.policy) if args.policy else DEFAULT_POLICY_PATH
    try:
        ctx = validate_startup(policy_path=policy_path, database_url=args.database_url)
    except StartupRefused as exc:
        out = {
            "command": args.command,
            "date": date.isoformat(),
            "error": exc.code,
            "message": str(exc),
            "requests_sent": 0,
            "exit_code": 2,
        }
        print(json.dumps(out, ensure_ascii=False))
        return 2
    from horseracing_db.session import create_db_engine

    engine = create_db_engine(args.database_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    nk_interval = _netkeiba_interval_from_policy(policy_path)

    def guard():
        with session_factory() as s:
            return active_hold(s, path=policy_path, interval_s=nk_interval)

    fetcher = JraFetcher(
        policy=ctx.policy,
        capture_guard=guard,
        archive_dir=Path(args.archive_dir) if args.archive_dir else DEFAULT_ARCHIVE_DIR,
        robots_cache_store=robots_cache.shared_cache(),
    )
    result = run_command(
        args.command, args, fetcher=fetcher, session_factory=session_factory, date=date
    )
    text = json.dumps(result.to_json(), ensure_ascii=False, indent=None if args.json else 2)
    print(text, file=sys.stdout)
    return result.exit_code


__all__ = [
    "COMMANDS",
    "RunResult",
    "register_jra_subcommands",
    "run_command",
    "run_jra",
    "KIND_ENTRIES",
    "KIND_ODDS_WIN",
]
