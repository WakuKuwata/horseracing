"""``scrape jrdb-*`` (Feature 178, T035; contracts/cli.md). US1 commands only:

* ``jrdb-fetch``   — member site → ``artifacts/jrdb/<kind>/<yyyymmdd>/*.txt``
  + ``jrdb_files(fetched)``
* ``jrdb-parity``  — files + DB (READ ONLY) → ``artifacts/jrdb/parity/<year>.json``
* ``jrdb-persons`` — KZA/CZA × official profiles → mapping report (``--dry-run`` only in US1;
  writing ``id_mappings`` is US2 / T047)

Registration in ``horseracing_scrape.cli`` is a one-line change to a shared module (tasks T036 ⏸),
so this module also exposes ``main(argv)`` for direct use and tests. Every command runs the startup
checks (``policy.validate_startup``) before the first request; ``jrdb-parity`` sends nothing.

Partial decodes are recorded, never hidden (codex D20 #6): ``rows_rejected`` in the job summary and
``rejected`` status when a file decodes to nothing.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ..pipeline import _run_job
from ..upsert import Counts
from . import files as jfiles
from . import held as jheld
from .archive import extract
from .blackout import JST
from .cli_write import WRITE_COMMANDS
from .decode import decode_file
from .errors import JrdbError, JrdbNotFound, JrdbStartupRefused
from .fetch import (
    JrdbFetcher,
    source_url_sha256,
    url_person_master,
    url_single,
    url_yearly,
)
from .models import JrdbFileMeta
from .parity import build_parity, jrdb_year_from_decoded, load_reference
from .policy import DEFAULT_POLICY_PATH, validate_startup

SOURCE = "jrdb"  # ingestion_jobs.source (Source.JRDB lands with the enum in R2 — T015 ⏸)
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ARTIFACTS = _REPO_ROOT / "artifacts" / "jrdb"
COMMANDS = (
    "jrdb-fetch",
    "jrdb-parity",
    "jrdb-persons",
    "jrdb-repair",
    "jrdb-rebuild",
    "jrdb-apply-held",
    "jrdb-persons-collect",
    *WRITE_COMMANDS,
)
DAILY_KINDS_DEFAULT = ("BAC", "KYI", "UKC", "HJC", "SED", "TYB")
ARCHIVE_KINDS_DEFAULT = ("BAC", "KYI", "UKC", "SED", "HJC")
PERSON_KINDS = ("KZA", "CZA")


# --------------------------------------------------------------------------- argparse


def register_jrdb_subcommands(sub: argparse._SubParsersAction) -> None:
    from .cli_write import register_write_subcommands

    register_write_subcommands(sub)
    f = sub.add_parser(
        "jrdb-fetch", help="JRDB 会員ページから取得して展開・jrdb_files に記録 (178)"
    )
    f.add_argument("--date", default=None, help="日次ファイルの日 YYYY-MM-DD")
    f.add_argument("--archive", action="store_true", help="年度パック (--from/--to の年)")
    f.add_argument("--from", dest="year_from", type=int, default=None)
    f.add_argument("--to", dest="year_to", type=int, default=None)
    f.add_argument(
        "--kinds",
        nargs="+",
        default=None,
        help="種別 (既定: 日次 BAC KYI UKC HJC SED TYB / 年度 BAC KYI UKC SED HJC)",
    )
    f.add_argument("--single", action="store_true", help="年度パックでなく単体 zip を日ごとに取る")
    f.add_argument(
        "--show-held",
        action="store_true",
        help="held 行と現行版の差分を表示して終わる (読み取り専用・要求 0 件・--date で絞る)",
    )
    f.add_argument("--database-url", default=None)
    f.add_argument("--policy", default=None, help=f"policy file (default {DEFAULT_POLICY_PATH})")
    f.add_argument("--artifacts-dir", default=None, help=f"展開先 (default {DEFAULT_ARTIFACTS})")
    f.add_argument("--json", action="store_true")

    p = sub.add_parser("jrdb-parity", help="段 1 parity 報告 (DB 読み取り専用・製品表に書かない)")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--files-dir", required=True, help="展開済みテキストの置き場 (絶対パス)")
    p.add_argument(
        "--out", default=None, help="出力 JSON (既定 artifacts/jrdb/parity/<year>.json・既存は拒否)"
    )
    p.add_argument("--db-url", default=None)
    p.add_argument(
        "--persons-report", default=None, help="jrdb-persons の report.json (省略時は全て unmapped)"
    )
    p.add_argument("--json", action="store_true")

    q = sub.add_parser(
        "jrdb-persons", help="KZA/CZA × JRA 公式プロフィール → 写像報告 (US1 は --dry-run のみ)"
    )
    q.add_argument("--files-dir", required=True)
    q.add_argument("--profiles", default=None, help="jra-profiles.jsonl")
    q.add_argument(
        "--codes-from",
        default=None,
        help="対象データの人物コード一覧 JSON (kind → {code: [years]})",
    )
    q.add_argument("--out", default=None)
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("--database-url", default=None)
    q.add_argument("--json", action="store_true")

    # Feature 178 US3 — 段 3a (current DB, one column) / 段 3b (the canonical DB, a range)
    r = sub.add_parser(
        "jrdb-repair",
        help="段 3a: アーカイブから現行 DB の 1 列を修復 (基準合格列のみ・値が変わる行だけ)",
    )
    r.add_argument("--column", required=True, help="allowlist の列名 (repair.REPAIRABLE_COLUMNS)")
    r.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    r.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    r.add_argument("--files-dir", required=True, help="展開済みアーカイブ (絶対パス)")
    r.add_argument("--parity-dir", required=True, help="段 1 の parity JSON の置き場 (絶対パス)")
    r.add_argument("--dry-run", action="store_true", help="全部やって巻き戻す (書込み 0)")
    r.add_argument(
        "--out-dir", default=None, help=f"報告 JSON (default {DEFAULT_ARTIFACTS}/repair)"
    )
    r.add_argument("--database-url", default=None)
    r.add_argument("--policy", default=None, help=f"policy file (default {DEFAULT_POLICY_PATH})")
    r.add_argument("--json", action="store_true")

    b = sub.add_parser(
        "jrdb-rebuild", help="段 3b: JRDB 正本の別 DB にアーカイブを取り込む (現行 DB は読むだけ)"
    )
    b.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    b.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    b.add_argument("--files-dir", required=True, help="展開済みアーカイブ (絶対パス)")
    b.add_argument("--db-url", required=True, help="書込み先 (現行 DB と同じなら拒否)")
    b.add_argument("--reference-db-url", required=True, help="比較に読む現行 DB")
    b.add_argument(
        "--out-dir", default=None, help=f"compare-<year>.json (default {DEFAULT_ARTIFACTS}/rebuild)"
    )
    b.add_argument("--json", action="store_true")

    # Feature 178 T073 — the operator's approval of a ``held`` file (manual.md §3)
    h = sub.add_parser(
        "jrdb-apply-held",
        help="held ファイルの運用者承認: 差分表示→--yes で現行版を superseded にし writer を再実行",
    )
    h.add_argument("file_id", help="jrdb_files.jrdb_file_id (jrdb-fetch --show-held が出す)")
    h.add_argument(
        "--profile",
        required=True,
        choices=["current", "jrdb_canonical"],
        help="書込み先プロファイル (既定なし)",
    )
    h.add_argument(
        "--yes", action="store_true", help="差分を読んだ上での承認 (無ければ表示だけで exit 2)"
    )
    h.add_argument(
        "--replaces",
        default=None,
        help="失効させる現行版の jrdb_file_id (--show-held の current)・無ければ none。違えば停止",
    )
    h.add_argument("--database-url", default=None)
    h.add_argument("--policy", default=None, help=f"policy file (default {DEFAULT_POLICY_PATH})")
    h.add_argument(
        "--artifacts-dir", default=None, help=f"展開済みテキスト (default {DEFAULT_ARTIFACTS})"
    )
    h.add_argument("--json", action="store_true")

    # Feature 178 R3 — the one-time collection of JRA official person pages (jra/person_profile.py)
    pc = sub.add_parser(
        "jrdb-persons-collect",
        help="JRA 公式の騎手/調教師ページを 1 回だけ集めて jra-profiles.jsonl に追記 (176 fetcher)",
    )
    pc.add_argument("--kind", required=True, choices=["jockey", "trainer"])
    pc.add_argument(
        "--index-url", required=True, help="名鑑一覧ページの URL (公開サイトの遷移から)"
    )
    pc.add_argument(
        "--index-cname",
        required=True,
        help="その一覧ページの cname (動的・保存しない・ログに出さない)",
    )
    pc.add_argument(
        "--out", default=None, help="jra-profiles.jsonl (default artifacts/jrdb/persons/)"
    )
    pc.add_argument("--max-pages", type=int, default=None, help="今回取る人物ページ数の上限")
    pc.add_argument("--database-url", default=None)
    pc.add_argument("--policy", default=None, help=f"policy file (default {DEFAULT_POLICY_PATH})")
    pc.add_argument("--json", action="store_true")


# --------------------------------------------------------------------------- results


@dataclass
class RunResult:
    command: str
    requests_sent: int = 0
    jobs: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    report_path: str | None = None
    exit_code: int = 0
    error: str | None = None
    held: list[dict] | None = None  # jrdb-fetch --show-held / jrdb-apply-held
    approval: dict | None = None  # jrdb-apply-held --yes

    def to_json(self) -> dict:
        out = {
            "command": self.command,
            "requests_sent": self.requests_sent,
            "jobs": self.jobs,
            "notes": self.notes,
            "report_path": self.report_path,
            "exit_code": self.exit_code,
            "error": self.error,
        }
        if self.held is not None:
            out["held"] = self.held
        if self.approval is not None:
            out["approval"] = self.approval
        return out


def _parse_date(s: str | None) -> datetime.date:
    if s is None:
        return datetime.datetime.now(JST).date()
    return datetime.date.fromisoformat(s)


def _db_race_days(session_factory, around: datetime.date) -> set[datetime.date]:
    with session_factory() as s:
        rows = s.execute(
            text("SELECT DISTINCT race_date FROM races WHERE race_date BETWEEN :a AND :b"),
            {"a": around - datetime.timedelta(days=7), "b": around + datetime.timedelta(days=7)},
        ).scalars()
        return {r for r in rows if r is not None}


# --------------------------------------------------------------------------- jrdb-fetch


def ingest_archive_bytes(
    session: Session,
    *,
    raw: bytes,
    name: str,
    variant: str,
    url: str,
    fetched_at: datetime.datetime,
    published_at: datetime.datetime | None,
    artifacts_dir: Path,
    counts: Counts,
    data_date: datetime.date | None = None,
    files_summary: list[dict],
) -> None:
    """One downloaded archive → extracted members → jrdb_files rows + saved text."""

    for member_name, member in extract(raw, name):
        if not member_name.lower().endswith(".txt"):
            counts.skipped += 1
            continue
        try:
            meta = jfiles.build_meta(
                member_name, member, variant=variant, fetched_at=fetched_at, data_date=data_date
            )
        except JrdbError as exc:
            counts.errors += 1
            counts.error_messages.append(f"{member_name}: {exc}")
            continue
        existing = jfiles.find_versions(session, meta.kind, meta.data_date, meta.variant)
        applied = [r for r in existing if r.status in ("applied", "partially_applied", "fetched")]
        prev_meta = None
        if applied:
            last = applied[-1]
            prev_meta = JrdbFileMeta(
                last.kind,
                meta.version,
                last.data_date,
                last.variant,
                last.sha256,
                last.size_bytes,
                last.record_length,
                edition=None,
            )
            if last.kind in ("SED", "SEC"):
                prev_meta = JrdbFileMeta(
                    **{
                        **prev_meta.__dict__,
                        "edition": jfiles.sed_edition(last.data_date, last.fetched_at),
                    }
                )
        verdict = jfiles.classify_version(prev_meta, meta)
        row, created = jfiles.record_fetched(
            session,
            meta,
            source_url_sha256=source_url_sha256(url),
            fetched_at=fetched_at,
            published_at=published_at,
        )
        path = jfiles.save_extracted(artifacts_dir, meta, member_name, member)
        counts.processed += 1
        if created:
            counts.written += 1
        else:
            counts.skipped += 1
        # a file whose records do not frame at all is marked rejected (decode fail-closed)
        try:
            decoded = decode_file(member, meta, profile="current")
            counts.extra["rows_read"] = counts.extra.get("rows_read", 0) + decoded.n_rows
            counts.extra["rows_rejected"] = counts.extra.get("rows_rejected", 0) + len(
                decoded.rejected_rows
            )
        except JrdbError as exc:
            jfiles.mark_rejected(session, row)
            counts.errors += 1
            counts.error_messages.append(f"{member_name}: {type(exc).__name__}")
        if verdict == "supersede" and applied:
            jfiles.mark_superseded(session, applied[-1])
            counts.extra["stale_version"] = counts.extra.get("stale_version", 0) + 1
        elif verdict == "held":
            jfiles.mark_held(session, row)
            counts.extra["held"] = counts.extra.get("held", 0) + 1
        files_summary.append(
            {
                "jrdb_file_id": str(row.jrdb_file_id),
                "kind": meta.kind,
                "data_date": meta.data_date.isoformat(),
                "variant": meta.variant,
                "sha256": meta.sha256,
                "published_at": published_at.isoformat() if published_at else None,
                "path": str(path),
                "verdict": verdict,
            }
        )


def run_fetch(args, *, fetcher: JrdbFetcher, session_factory, artifacts_dir: Path) -> RunResult:
    result = RunResult("jrdb-fetch")
    kinds = tuple(
        k.upper()
        for k in (args.kinds or (ARCHIVE_KINDS_DEFAULT if args.archive else DAILY_KINDS_DEFAULT))
    )
    targets: list[
        tuple[str, str, str, datetime.date | None]
    ] = []  # (url, name, variant, data_date)
    if args.archive:
        if args.year_from is None:
            raise SystemExit("--archive needs --from (and optionally --to)")
        for year in range(args.year_from, (args.year_to or args.year_from) + 1):
            for k in kinds:
                targets.append((url_yearly(k, year), f"{k}_{year}.zip", "archive", None))
    else:
        day = _parse_date(args.date)
        for k in kinds:
            if k in PERSON_KINDS:
                url = url_person_master(k, day)
            else:
                url = url_single(k, day)
            targets.append((url, url.rsplit("/", 1)[-1], "daily", day))
    fetched_at = datetime.datetime.now(datetime.UTC)
    files_summary: list[dict] = []

    for url, name, variant, data_date in targets:
        with session_factory() as session:

            def work(job_id, _url=url, _name=name, _variant=variant, _dd=data_date) -> Counts:
                counts = Counts()
                try:
                    got = fetcher.get(_url)
                except JrdbNotFound:
                    counts.skipped += 1
                    counts.extra["not_published"] = 1
                    return counts
                if got is None:
                    counts.skipped += 1
                    return counts
                ingest_archive_bytes(
                    session,
                    raw=got.raw,
                    name=_name,
                    variant=_variant,
                    url=_url,
                    fetched_at=got.fetched_at,
                    published_at=got.last_modified,
                    artifacts_dir=artifacts_dir,
                    counts=counts,
                    data_date=_dd,
                    files_summary=files_summary,
                )
                counts.extra["files"] = list(files_summary)
                return counts

            summary = _run_job(
                session,
                job_type="jrdb_fetch",
                scope="file",
                scope_value=name,
                work=work,
                source=SOURCE,
                pass_job_id=True,
            )
            result.jobs.append(
                {
                    "file": name,
                    "status": summary.status,
                    "written": summary.written,
                    "skipped": summary.skipped,
                    "errors": summary.errors,
                    **(summary.extra or {}),
                }
            )
            if summary.status == "failed":
                result.exit_code = 1
                break  # a refusal ends the run (contract)
    result.requests_sent = fetcher.requests_sent
    del fetched_at
    return result


# --------------------------------------------------------------------------- held (T073)


def run_show_held(args, *, session_factory, artifacts_dir: Path) -> RunResult:
    """``jrdb-fetch --show-held``: every ``held`` row (optionally one day) with the version it
    would replace and the row diff. READ ONLY — no request, no job row."""

    result = RunResult("jrdb-fetch --show-held")
    day = datetime.date.fromisoformat(args.date) if getattr(args, "date", None) else None
    with session_factory() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            rows = jheld.list_held(session, day)
            result.held = [jheld.describe(session, artifacts_dir, r) for r in rows]
        finally:
            session.rollback()
    if not result.held:
        result.notes.append("no held files" + (f" for {day}" if day else ""))
    return result


def run_apply_held(
    args, *, session_factory, artifacts_dir: Path, production_url: str | None
) -> RunResult:
    """``jrdb-apply-held FILE_ID --profile P [--yes]``: show the diff; with ``--yes`` make the
    held row the version to apply and run the kind's writer command on that day (the writer's
    rules decide what changes — first-writer items are kept). Without ``--yes`` nothing is
    written and the exit code is 2."""

    from .cli_write import run_write
    from .policy import assert_profile_binding, read_policy, require_enabled

    result = RunResult("jrdb-apply-held")
    policy_path = Path(args.policy) if getattr(args, "policy", None) else DEFAULT_POLICY_PATH
    try:
        require_enabled(read_policy(policy_path))
        assert_profile_binding(args.profile, args.database_url, production_url)
    except JrdbStartupRefused as exc:
        result.exit_code, result.error = 2, f"{exc.code}: {exc}"
        return result
    now = datetime.datetime.now(datetime.UTC)
    with session_factory() as session:
        try:
            row = jheld.load_row(session, args.file_id)
            if row.status != "held":
                raise jheld.HeldNotApprovable("not_held", f"{row.jrdb_file_id} is {row.status}")
            desc = jheld.describe(session, artifacts_dir, row, profile=args.profile)
            result.held = [desc]
            if desc["error"]:  # any incomplete comparison blocks the approval (codex D26 #8)
                raise jheld.HeldNotApprovable("held_diff_incomplete", desc["error"])
            if not args.yes:
                session.rollback()
                result.exit_code = 2
                result.error = "confirmation_required: read the diff, then re-run with --yes"
                return result
            result.approval = jheld.approve(
                session, row, now=now, replaces=getattr(args, "replaces", None)
            )
            session.commit()
            kind, day = row.kind, row.data_date
            variant = row.variant
        except jheld.HeldNotApprovable as exc:
            session.rollback()
            result.exit_code, result.error = 2, str(exc)
            return result
    command = jheld.COMMAND_FOR_KIND.get(kind)
    if command is None:
        result.notes.append(f"{kind} has no writer command; approval recorded, nothing re-applied")
        return result
    venue = variant.split("_", 1)[1].upper() if variant.startswith("venue_") else None
    write_args = argparse.Namespace(
        command=command,
        date=day.isoformat(),
        profile=args.profile,
        venue=venue,
        database_url=args.database_url,
        policy=args.policy,
        artifacts_dir=str(artifacts_dir),
        json=args.json,
    )
    wr = run_write(
        write_args,
        session_factory=session_factory,
        artifacts=artifacts_dir,
        production_url=production_url,
    )
    result.jobs.extend(wr.jobs)
    result.notes.extend(wr.notes)
    if wr.error:
        result.error = wr.error
    result.exit_code = wr.exit_code
    return result


# --------------------------------------------------------------------------- persons-collect (R3)


def wanted_person_ids(session: Session, kind: str) -> list[str]:
    """Every official (5-digit) id the DB knows for ``kind``; ``nk:`` surrogates have no official
    page and are left out."""

    table, col = ("jockeys", "jockey_id") if kind == "jockey" else ("trainers", "trainer_id")
    rows = session.execute(text(f"SELECT {col} FROM {table}")).scalars()  # noqa: S608 — fixed names
    return sorted(r for r in rows if r and len(r) == 5 and r.isdigit())


def run_persons_collect(args, *, fetcher, session_factory) -> RunResult:
    """Append the profile of every wanted id not yet collected (``collect_profiles`` stops at the
    first refusal; a stop other than ``max_pages`` is exit 1 so the operator re-runs later — the
    file is append-only and ids already present are never fetched again)."""

    import dataclasses as dc

    from ..jra.person_profile import DEFAULT_PROFILES_PATH, collect_profiles

    result = RunResult("jrdb-persons-collect")
    with session_factory() as session:
        wanted = wanted_person_ids(session, args.kind)
    out = Path(args.out) if args.out else DEFAULT_PROFILES_PATH
    report = collect_profiles(
        fetcher,
        kind=args.kind,
        index=(args.index_url, args.index_cname),
        wanted_ids=wanted,
        out_path=out,
        max_pages=args.max_pages,
    )
    result.jobs.append({"kind": args.kind, **dc.asdict(report)})
    result.report_path = str(out)
    result.requests_sent = int(getattr(fetcher, "requests_sent", 0))
    if report.stopped and report.stopped != "max_pages":
        result.exit_code = 1
        result.error = f"stopped: {report.stopped}"
    return result


def _jra_fetcher(args, policy_path: Path):
    """The 176 fetcher exactly as ``jra/cli.py`` builds it (startup checks first; a refusal is
    raised as ``StartupRefused``)."""

    from .. import robots_cache
    from ..capture_window import active_hold
    from ..jra.cli import DEFAULT_ARCHIVE_DIR, _netkeiba_interval_from_policy
    from ..jra.fetch import JraFetcher
    from ..jra.policy import validate_startup

    ctx = validate_startup(policy_path=policy_path, database_url=args.database_url)
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
        archive_dir=DEFAULT_ARCHIVE_DIR,
        robots_cache_store=robots_cache.shared_cache(),
    )
    return fetcher, session_factory


# --------------------------------------------------------------------------- jrdb-parity


def load_files_dir(files_dir: Path, *, year: int) -> list:
    """Decode every ``*.txt`` under ``files_dir`` (any depth) that belongs to ``year`` under the
    canonical profile (the report COMPARES the four JRDB-defined columns; it writes nothing)."""

    out = []
    for path in sorted(files_dir.rglob("*.txt")):
        try:
            kind, version, day, _variant = jfiles.parse_file_name(
                path.name, reference=datetime.date(year, 7, 1)
            )
        except JrdbError:
            continue
        if day.year != year and kind not in PERSON_KINDS:
            continue
        raw = path.read_bytes()
        meta = jfiles.build_meta(
            path.name, raw, variant="archive", fetched_at=datetime.datetime.now(datetime.UTC)
        )
        try:
            out.append(decode_file(raw, meta, profile="jrdb_canonical"))
        except JrdbError as exc:
            out.append(exc)
    return out


def run_parity(args, *, session_factory) -> RunResult:
    result = RunResult("jrdb-parity")
    files_dir = Path(args.files_dir)
    if not files_dir.is_absolute():
        raise SystemExit("--files-dir must be absolute")
    out_path = Path(args.out) if args.out else DEFAULT_ARTIFACTS / "parity" / f"{args.year}.json"
    if out_path.exists():
        result.exit_code = 2
        result.error = f"refusing to overwrite {out_path}"
        return result
    decoded = load_files_dir(files_dir, year=args.year)
    errors = [d for d in decoded if isinstance(d, Exception)]
    good = [d for d in decoded if not isinstance(d, Exception)]
    for e in errors:
        result.notes.append(f"decode error: {type(e).__name__}: {e}")
    year = jrdb_year_from_decoded(good)
    pm: dict[tuple[str, str], str] = {}
    if args.persons_report:
        doc = json.loads(Path(args.persons_report).read_text(encoding="utf-8"))
        for kind, codes in doc.get("matches", {}).items():
            for code, status in codes.items():
                pm[(kind, code)] = status
    with session_factory() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            ref = load_reference(session, args.year)
        finally:
            session.rollback()
    report = build_parity(args.year, year, ref, person_mapping=pm)
    report["rejected_rows"] = sum(len(d.rejected_rows) for d in good)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    result.report_path = str(out_path)
    return result


# --------------------------------------------------------------------------- jrdb-persons


def run_persons(args) -> RunResult:
    from ..jra.person_profile import load_profiles
    from .persons import build_report

    result = RunResult("jrdb-persons")
    files_dir = Path(args.files_dir)
    persons = []
    for path in sorted(files_dir.rglob("*.txt")):
        if path.name[:3].upper() not in ("KZA", "KSA", "CZA", "CSA"):
            continue
        raw = path.read_bytes()
        meta = jfiles.build_meta(
            path.name, raw, variant="daily", fetched_at=datetime.datetime.now(datetime.UTC)
        )
        persons.extend(decode_file(raw, meta, profile="current").rows)
    profiles = list(load_profiles(args.profiles).values()) if args.profiles else []
    codes: dict[str, dict[str, set[int]]] = {"jockey": {}, "trainer": {}}
    if args.codes_from:
        doc = json.loads(Path(args.codes_from).read_text(encoding="utf-8"))
        for kind, m in doc.items():
            codes[kind] = {c: set(int(y) for y in ys) for c, ys in m.items()}
    rep = build_report(persons, profiles, codes_in_data=codes)
    doc = rep.to_json()
    doc["matches"] = {
        kind: {code: m.status for (k, code), m in rep.matches.items() if k == kind}
        for kind in ("jockey", "trainer")
    }
    out_path = Path(args.out) if args.out else DEFAULT_ARTIFACTS / "persons" / "report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result.report_path = str(out_path)
    if not args.dry_run:  # T047: id_mappings(source=jrdb)
        from horseracing_db.session import create_db_engine
        from sqlalchemy.orm import sessionmaker

        from .persons import write_id_mappings

        engine = create_db_engine(getattr(args, "database_url", None))
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            counts = write_id_mappings(session, rep, dry_run=False)
            session.commit()
        result.jobs.append({"id_mappings": counts})
    return result


# --------------------------------------------------------------------------- jrdb-repair (段 3a)


def run_repair(args, *, session_factory, production_url: str | None = None) -> RunResult:
    """One column, one range. Startup: policy ``jrdb_enabled``; the allowlist and the stage-1
    verdicts are checked by ``repair.assert_repairable`` before any file is read. A real run is one
    audited ``ingestion_jobs`` row (``jrdb_repair``) whose ``summary.files[]`` carries the archive
    files' times (data-model §3a); ``--dry-run`` does the whole pass and rolls it back."""

    from .policy import read_policy, require_enabled
    from .repair import (
        RepairColumnRefused,
        assert_repairable,
        repair_column,
        total_fill,
        write_report,
    )

    result = RunResult("jrdb-repair")
    policy_path = Path(args.policy) if getattr(args, "policy", None) else DEFAULT_POLICY_PATH
    try:
        require_enabled(read_policy(policy_path))
    except JrdbStartupRefused as exc:
        result.exit_code, result.error = 2, f"{exc.code}: {exc}"
        return result
    files_dir, parity_dir = Path(args.files_dir), Path(args.parity_dir)
    if not files_dir.is_absolute() or not parity_dir.is_absolute():
        result.exit_code, result.error = 2, "--files-dir and --parity-dir must be absolute"
        return result
    try:  # the allowlist and the stage-1 verdicts, before a job row or a DB read exists
        assert_repairable(args.column, parity_dir)
    except RepairColumnRefused as exc:
        result.exit_code, result.error = 2, f"{exc.reason}: {exc}"
        return result
    d0, d1 = _parse_date(args.date_from), _parse_date(args.date_to)
    if d1 < d0:
        result.exit_code, result.error = 2, "--to is before --from"
        return result
    out_dir = Path(args.out_dir) if getattr(args, "out_dir", None) else DEFAULT_ARTIFACTS / "repair"
    holder: dict = {}
    with session_factory() as session:
        try:
            if args.dry_run:
                holder["report"] = repair_column(
                    session,
                    args.column,
                    d0,
                    d1,
                    files_dir=files_dir,
                    parity_dir=parity_dir,
                    dry_run=True,
                )
                session.rollback()
            else:

                def work(job_id) -> Counts:
                    rep = repair_column(
                        session,
                        args.column,
                        d0,
                        d1,
                        files_dir=files_dir,
                        parity_dir=parity_dir,
                        dry_run=False,
                        job_id=job_id,
                    )
                    holder["report"] = rep
                    return rep.counts()

                summary = _run_job(
                    session,
                    job_type="jrdb_repair",
                    scope="range",
                    scope_value=f"{args.column}:{d0.isoformat()}..{d1.isoformat()}",
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
                    }
                )
                if summary.status == "failed":
                    result.exit_code = 1
        except RepairColumnRefused as exc:
            session.rollback()
            result.exit_code, result.error = 2, f"{exc.reason}: {exc}"
            return result
    rep = holder.get("report")
    if rep is not None:
        result.report_path = str(write_report(rep, out_dir))
        result.notes.append(
            f"{rep.column}: changed={rep.rows_changed} unchanged={rep.rows_unchanged} "
            f"fill {total_fill(rep.by_year_fill_before)} -> {total_fill(rep.by_year_fill_after)}"
            + (" (dry-run, rolled back)" if rep.dry_run else "")
        )
        result.notes.extend(rep.notes)
        if rep.errors:
            result.notes.extend(rep.errors)
            result.exit_code = max(result.exit_code, 1)
    return result


# --------------------------------------------------------------------------- jrdb-rebuild (段 3b)


def run_rebuild(args, *, env_url: str | None) -> RunResult:
    from .rebuild import rebuild_range

    result = RunResult("jrdb-rebuild")
    files_dir = Path(args.files_dir)
    if not files_dir.is_absolute():
        result.exit_code, result.error = 2, "--files-dir must be absolute"
        return result
    d0, d1 = _parse_date(args.date_from), _parse_date(args.date_to)
    if d1 < d0:
        result.exit_code, result.error = 2, "--to is before --from"
        return result
    out_dir = (
        Path(args.out_dir) if getattr(args, "out_dir", None) else DEFAULT_ARTIFACTS / "rebuild"
    )
    rep = rebuild_range(
        files_dir,
        d0,
        d1,
        db_url=args.db_url,
        reference_db_url=args.reference_db_url,
        out_dir=out_dir,
        env_url=env_url,
    )
    result.exit_code, result.error = rep.exit_code, rep.error
    result.jobs = [j for y in rep.years for j in y.jobs]
    result.notes.append(
        f"years={[y.year for y in rep.years]} blocking_years={rep.blocking_years} "
        f"schema={rep.schema_head} person_map={rep.person_map_size}"
    )
    for y in rep.years:
        result.notes.append(
            f"{y.year}: jrdb {y.counts_jrdb} current {y.counts_current} blocking={y.blocking}"
        )
    result.notes.extend(rep.skipped_files[:20])
    result.report_path = ";".join(rep.report_paths) or None
    return result


# --------------------------------------------------------------------------- entry


def run_jrdb(args) -> int:
    policy_path = Path(args.policy) if getattr(args, "policy", None) else DEFAULT_POLICY_PATH
    if args.command in WRITE_COMMANDS:
        from .cli_write import run_write_command

        return run_write_command(args)
    if args.command == "jrdb-persons":
        result = run_persons(args)
    elif args.command == "jrdb-repair":
        from horseracing_db.session import create_db_engine

        engine = create_db_engine(args.database_url)
        result = run_repair(
            args,
            session_factory=sessionmaker(bind=engine, expire_on_commit=False),
            production_url=os.environ.get("DATABASE_URL"),
        )
    elif args.command == "jrdb-rebuild":
        result = run_rebuild(args, env_url=os.environ.get("DATABASE_URL"))
    elif args.command == "jrdb-persons-collect":
        from ..jra.policy import StartupRefused

        try:
            fetcher, session_factory = _jra_fetcher(args, policy_path)
        except StartupRefused as exc:
            print(
                json.dumps(
                    {
                        "command": args.command,
                        "error": exc.code,
                        "message": str(exc),
                        "requests_sent": 0,
                        "exit_code": 2,
                    },
                    ensure_ascii=False,
                )
            )
            return 2
        result = run_persons_collect(args, fetcher=fetcher, session_factory=session_factory)
    elif args.command == "jrdb-apply-held":
        from horseracing_db.session import create_db_engine

        engine = create_db_engine(args.database_url)
        result = run_apply_held(
            args,
            session_factory=sessionmaker(bind=engine, expire_on_commit=False),
            artifacts_dir=Path(args.artifacts_dir) if args.artifacts_dir else DEFAULT_ARTIFACTS,
            production_url=os.environ.get("DATABASE_URL"),
        )
    elif args.command == "jrdb-parity":
        from horseracing_db.session import create_db_engine

        engine = create_db_engine(args.db_url)
        result = run_parity(args, session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    elif args.command == "jrdb-fetch":
        from horseracing_db.session import create_db_engine

        engine = create_db_engine(args.database_url)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        artifacts_dir = Path(args.artifacts_dir) if args.artifacts_dir else DEFAULT_ARTIFACTS
        if args.show_held:  # read only: no credentials, no request, no job row
            result = run_show_held(
                args, session_factory=session_factory, artifacts_dir=artifacts_dir
            )
            print(json.dumps(result.to_json(), ensure_ascii=False, indent=None if args.json else 2))
            return result.exit_code
        try:
            ctx = validate_startup(
                policy_path=policy_path,
                needs_credentials=True,
                is_archive=bool(args.archive),
                db_race_days=_db_race_days(session_factory, _parse_date(args.date)),
            )
        except JrdbStartupRefused as exc:
            print(
                json.dumps(
                    {
                        "command": args.command,
                        "error": exc.code,
                        "message": str(exc),
                        "requests_sent": 0,
                        "exit_code": 2,
                    },
                    ensure_ascii=False,
                )
            )
            return 2
        assert ctx.credentials is not None
        fetcher = JrdbFetcher(credentials=ctx.credentials, min_interval_s=ctx.interval_s)
        result = run_fetch(
            args, fetcher=fetcher, session_factory=session_factory, artifacts_dir=artifacts_dir
        )
    else:
        raise SystemExit(f"unknown command {args.command}")
    print(
        json.dumps(result.to_json(), ensure_ascii=False, indent=None if args.json else 2),
        file=sys.stdout,
    )
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing-scrape jrdb")
    sub = parser.add_subparsers(dest="command", required=True)
    register_jrdb_subcommands(sub)
    args = parser.parse_args(argv)
    return run_jrdb(args)


__all__ = [
    "COMMANDS",
    "RunResult",
    "ingest_archive_bytes",
    "load_files_dir",
    "main",
    "register_jrdb_subcommands",
    "run_apply_held",
    "run_fetch",
    "run_jrdb",
    "run_persons_collect",
    "run_show_held",
    "wanted_person_ids",
    "run_parity",
    "run_persons",
    "run_rebuild",
    "run_repair",
]


if __name__ == "__main__":  # ``python -m horseracing_scrape.jrdb.cli`` (scripts/jrdb_ops.sh)
    sys.exit(main())
