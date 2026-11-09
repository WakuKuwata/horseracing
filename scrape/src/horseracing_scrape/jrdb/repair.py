"""段 3a — column-by-column repair of the CURRENT DB from the JRDB archive (Feature 178 US3, T057).

What it is: for ONE column at a time, the netkeiba-era holes (owner / breeder at 0 %, class
spelling variants, pre-race odds left as "final", NULL post times) are filled from the archive
files, through the same rules the writers use. What it is not: a second writer. It never creates a
row, never touches a JRA-VAN-era value that is already there, never changes FEATURE_VERSION, and
the five JRDB-defined physical columns are refused up front (FR-005 / FR-021a).

Allowlist (``REPAIRABLE_COLUMNS``) — every entry is a column whose 段 1 parity rule is
``same`` / ``normalized`` (definition-equal) or ``jrdb_only`` (post_time). At run time the stage-1
reports under ``parity_dir`` are read and a column whose rate is below its contract threshold in
ANY report is refused as well (``RepairColumnRefused``): the contract's numbers live in
``parity.COLUMN_RULES``, not here.

Rules per column (contracts/precedence-jrdb.md §3, the 139 final-odds rules):

* ``odds`` (with ``popularity``): SED 正式版 → ``apply_final_odds`` (netkeiba era only, value-
  changing rows only, canonical ids through the FR-011a alias rule).
* ``weight``: SED → the body-weight pair through ``field_transition`` (netkeiba era only).
* ``last_3f``: SED → fill NULL only (any era; same definition, the DB's own anomalies stay).
* ``owner_name`` / ``breeder_name``: UKC → fill NULL on every era; a netkeiba-owned master
  (``horses.data_source = 'netkeiba'``, including the ``nk:`` surrogates) takes the JRDB spelling;
  a JRA-VAN / official / JRDB-owned value is kept.
* ``race_class`` / ``prize_money`` / ``post_time``: BAC → netkeiba era through
  ``field_transition(race_static | post_time)`` (official-owned keeps, netkeiba-owned is
  overwritten + ``static_mismatch``); JRA-VAN era fills NULL only.
* ``win_payouts``: HJC → ``upsert_official_win_payouts(source='jrdb_result')`` (netkeiba era).

``dry_run`` performs the whole pass inside the caller's transaction and the CLI rolls it back, so
the counts are exact and nothing is written. Each race is its own SAVEPOINT.
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from horseracing_db.enums import Source
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from .. import precedence as P
from ..jra import alias as A
from ..models import ScrapedResultRow, ScrapedWinPayout
from ..provenance import write_horse_provenance, write_race_provenance
from ..upsert import (
    NETKEIBA_FINAL_ODDS_FROM,
    OFFICIAL_WIN_PAYOUT_SOURCE_JRDB,
    Counts,
    apply_final_odds,
    lock_race,
    upsert_official_win_payouts,
)
from . import files as jfiles
from . import normalize as nz
from . import parity
from .decode import DecodedFile, decode_file
from .errors import JrdbError
from .ids import horse_id_from_blood_no, race_id_from_key
from .profile import CURRENT, check_payload
from .upsert_jrdb import JST, RACE_CLASS_DB

JRDB = P.JRDB


class RepairColumnRefused(JrdbError):
    """The column is outside the allowlist, JRDB-defined, or below its stage-1 threshold."""

    def __init__(self, column: str, reason: str, detail: str = ""):
        self.column, self.reason, self.detail = column, reason, detail
        super().__init__(f"{column}: {reason}" + (f" ({detail})" if detail else ""))


@dataclass(frozen=True)
class ColumnSpec:
    table: str
    kinds: tuple[str, ...]
    #: the stage-1 parity columns whose verdict gates this repair
    parity_columns: tuple[str, ...]


#: the allowlist (contract: 基準合格列のみ). ``popularity`` rides with ``odds`` (one SED cell pair).
REPAIRABLE_COLUMNS: dict[str, ColumnSpec] = {
    "odds": ColumnSpec("race_horses", ("SED",), ("odds", "popularity")),
    "popularity": ColumnSpec("race_horses", ("SED",), ("odds", "popularity")),
    "weight": ColumnSpec("race_horses", ("SED",), ("weight", "weight_diff")),
    "last_3f": ColumnSpec("race_results", ("SED",), ("last_3f",)),
    "owner_name": ColumnSpec("horses", ("UKC",), ("owner_name",)),
    "breeder_name": ColumnSpec("horses", ("UKC",), ("breeder_name",)),
    "race_class": ColumnSpec("races", ("BAC",), ("race_class",)),
    "prize_money": ColumnSpec("races", ("BAC",), ("prize_money",)),
    "post_time": ColumnSpec("races", ("BAC",), ("post_time",)),
    "win_payouts": ColumnSpec("official_win_payouts", ("HJC",), ("win_payout",)),
}

#: the physical columns each allowlist entry writes (codex D23 #7: the unit is a cell pair where
#: the file carries a pair — the report says which columns a run may touch)
WRITES: dict[str, tuple[str, ...]] = {
    "odds": ("odds", "popularity"),
    "popularity": ("odds", "popularity"),
    "weight": ("weight", "weight_diff"),
    "last_3f": ("last_3f",),
    "owner_name": ("owner_name",),
    "breeder_name": ("breeder_name",),
    "race_class": ("race_class",),
    "prize_money": ("prize_money",),
    "post_time": ("post_time",),
    "win_payouts": ("official_win_payouts.*",),
}

#: refused before anything else is looked at (FR-005 / FR-021a)
REFUSED_COLUMNS: frozenset[str] = parity.DEFINITION_MISMATCH_COLUMNS


def assert_repairable(column: str, parity_dir: Path | None) -> dict[str, dict[str, float | None]]:
    """Allowlist + stage-1 verdicts. Returns ``{parity_column: {report: rate}}`` for the report.

    A column is refused when it is JRDB-defined, not in the allowlist, when no stage-1 report
    exists, or when any report's verdict for one of its parity columns is ``False``. A verdict of
    ``None`` (no compared cell / ``jrdb_only``) does not refuse: nothing was measured against it.
    """

    if column in REFUSED_COLUMNS:
        raise RepairColumnRefused(column, "definition_mismatch", "JRDB の定義が違う列 (FR-005)")
    spec = REPAIRABLE_COLUMNS.get(column)
    if spec is None:
        raise RepairColumnRefused(column, "not_in_allowlist")
    if parity_dir is None:
        raise RepairColumnRefused(column, "no_parity_dir")
    reports = sorted(Path(parity_dir).glob("*.json"))
    if not reports:
        raise RepairColumnRefused(column, "no_parity_report", str(parity_dir))
    rates: dict[str, dict[str, float | None]] = {c: {} for c in spec.parity_columns}
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        verdict = parity.passes(report)
        for pc in spec.parity_columns:
            col = report["columns"].get(pc)
            rates[pc][path.stem] = col.get("rate") if col else None
            if verdict.get(pc) is False:
                raise RepairColumnRefused(
                    column, "parity_below_threshold", f"{pc} in {path.name}: {col.get('rate')}"
                )
            # codex D23 #1: an UNDECIDED column (absent, or no compared cell) is not a passed
            # column. The one exception is the contract's ``jrdb_only`` meaning (post_time: the
            # DB is NULL before 2026, so stage 1 has nothing to compare it with by design).
            if verdict.get(pc) is None and parity.COLUMN_RULES[pc]["meaning"] != "jrdb_only":
                raise RepairColumnRefused(
                    column, "parity_undecided", f"{pc} in {path.name}: no compared cell"
                )
    return rates


# --------------------------------------------------------------------------- the archive files


@dataclass(frozen=True)
class RepairFile:
    path: Path
    meta: object  # JrdbFileMeta
    decoded: DecodedFile
    fetched_at: datetime.datetime  # the file's mtime: the only time an archive file carries

    def summary(self) -> dict:
        return {
            "jrdb_file_id": None,
            "kind": self.meta.kind,
            "data_date": self.meta.data_date.isoformat(),
            "variant": self.meta.variant,
            "sha256": self.meta.sha256,
            "published_at": None,
            "fetched_at": self.fetched_at.isoformat(),
            # codex D23 #9: an mtime is NOT a fetch time — say so, so a reader never takes it as
            # proof of when the value was available
            "fetched_at_basis": "file_mtime",
            "path": self.path.name,
        }


def load_archive_files(
    files_dir: Path,
    kinds: Iterable[str],
    date_from: datetime.date,
    date_to: datetime.date,
    *,
    profile: str = CURRENT.name,
) -> tuple[list[RepairFile], list[str]]:
    """Every ``<KIND>yymmdd.txt`` under ``files_dir`` (any depth) of the wanted kinds whose day is
    in range, decoded as ``variant='archive'`` (so a SED file is the 正式版 by contract §4)."""

    wanted = set(kinds)
    out: list[RepairFile] = []
    errors: list[str] = []
    years = range(date_from.year, date_to.year + 1)
    for path in sorted(Path(files_dir).rglob("*.txt")):
        parsed = None
        for year in years:
            try:
                kind, _v, day, _var = jfiles.parse_file_name(
                    path.name, reference=datetime.date(year, 7, 1)
                )
            except JrdbError:
                break
            if day.year == year:
                parsed = (kind, day)
                break
        if parsed is None:
            continue
        kind, day = parsed
        if kind not in wanted or not (date_from <= day <= date_to):
            continue
        raw = path.read_bytes()
        mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.UTC)
        try:
            meta = jfiles.build_meta(path.name, raw, variant="archive", fetched_at=mtime)
            out.append(RepairFile(path, meta, decode_file(raw, meta, profile=profile), mtime))
        except JrdbError as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return out, errors


# --------------------------------------------------------------------------- fill rates


_FILL_SQL: dict[str, str] = {
    "races": """
        SELECT extract(year FROM race_date)::int AS y, count(*) AS n, count({col}) AS filled
        FROM races WHERE race_date BETWEEN :d0 AND :d1 GROUP BY 1 ORDER BY 1""",
    "race_horses": """
        SELECT extract(year FROM r.race_date)::int AS y, count(*) AS n, count(rh.{col}) AS filled
        FROM race_horses rh JOIN races r USING (race_id)
        WHERE r.race_date BETWEEN :d0 AND :d1 GROUP BY 1 ORDER BY 1""",
    "race_results": """
        SELECT extract(year FROM r.race_date)::int AS y, count(*) AS n, count(rr.{col}) AS filled
        FROM race_results rr JOIN races r USING (race_id)
        WHERE r.race_date BETWEEN :d0 AND :d1 GROUP BY 1 ORDER BY 1""",
    "horses": """
        SELECT extract(year FROM r.race_date)::int AS y, count(*) AS n, count(h.{col}) AS filled
        FROM race_horses rh JOIN races r USING (race_id) JOIN horses h USING (horse_id)
        WHERE r.race_date BETWEEN :d0 AND :d1 GROUP BY 1 ORDER BY 1""",
    "official_win_payouts": """
        SELECT extract(year FROM r.race_date)::int AS y, count(*) AS n,
               count(p.race_id) AS filled
        FROM races r
        LEFT JOIN (SELECT DISTINCT race_id FROM official_win_payouts) p USING (race_id)
        WHERE r.race_date BETWEEN :d0 AND :d1
          AND EXISTS (SELECT 1 FROM race_results rr WHERE rr.race_id = r.race_id)
        GROUP BY 1 ORDER BY 1""",
}


def fill_by_year(
    session: Session, column: str, date_from: datetime.date, date_to: datetime.date
) -> dict[str, dict[str, float | int | None]]:
    """``{year: {n, filled, rate}}`` for the column's table over the range (rows of the race
    year; for ``horses`` the entries that point at a master, for payouts the settled races)."""

    spec = REPAIRABLE_COLUMNS[column]
    col = "payout_yen" if column == "win_payouts" else column
    sql = _FILL_SQL[spec.table].format(col=col)
    rows = session.execute(text(sql), {"d0": date_from, "d1": date_to}).all()
    return {
        str(y): {"n": int(n), "filled": int(f), "rate": (int(f) / int(n)) if n else None}
        for y, n, f in rows
    }


# --------------------------------------------------------------------------- the report


@dataclass
class RepairReport:
    column: str
    date_from: datetime.date
    date_to: datetime.date
    dry_run: bool
    rows_changed: int = 0
    rows_unchanged: int = 0
    races_seen: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    #: the physical columns this run may write (odds also writes popularity; weight the pair)
    columns_written: tuple[str, ...] = ()
    parity_rates: dict = field(default_factory=dict)
    by_year_fill_before: dict = field(default_factory=dict)
    by_year_fill_after: dict = field(default_factory=dict)
    files: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def bump(self, key: str, n: int = 1) -> None:
        self.skipped[key] = self.skipped.get(key, 0) + n

    def to_json(self) -> dict:
        return {
            "column": self.column,
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "dry_run": self.dry_run,
            "rows_changed": self.rows_changed,
            "rows_unchanged": self.rows_unchanged,
            "races_seen": self.races_seen,
            "skipped": self.skipped,
            "columns_written": list(self.columns_written),
            "parity_rates": self.parity_rates,
            "by_year_fill_before": self.by_year_fill_before,
            "by_year_fill_after": self.by_year_fill_after,
            "files": self.files,
            "notes": self.notes,
            "errors": self.errors,
        }

    def counts(self) -> Counts:
        """The ingestion_jobs view of this run."""

        c = Counts()
        c.processed = self.races_seen
        c.written = self.rows_changed
        c.skipped = sum(self.skipped.values())
        c.errors = len(self.errors)
        c.error_messages.extend(self.errors)
        c.extra["files"] = self.files  # type: ignore[assignment]
        c.extra["rows_changed"] = self.rows_changed
        c.extra["rows_unchanged"] = self.rows_unchanged
        for k, v in self.skipped.items():
            c.extra[k] = v
        return c


def _rid(key, year: int) -> str | None:
    try:
        return race_id_from_key(key, file_year=year)
    except Exception:  # noqa: BLE001
        return None


def _hid(blood: str, race_year: int, birth=None) -> str | None:
    try:
        return horse_id_from_blood_no(blood, race_year=race_year, birth_date=birth)
    except Exception:  # noqa: BLE001
        return None


def _netkeiba_era(race_date: datetime.date | None) -> bool:
    return race_date is not None and race_date >= NETKEIBA_FINAL_ODDS_FROM


def _rows_by_race(decoded_rows, year: int) -> dict[str, list]:
    by: dict[str, list] = {}
    for r in decoded_rows:
        rid = _rid(r.key, year)
        if rid is not None:
            by.setdefault(rid, []).append(r)
    return by


# --------------------------------------------------------------------------- per-column passes


def _sed_aliases(session: Session, snap, rows, race_year: int):
    official = [
        A.OfficialHorse(_hid(r.blood_reg_no, race_year) or "?", r.horse_number, r.horse_name)
        for r in rows
    ]
    aliases = A.resolve_horses(session, snap.horses, official, require_all=True)
    if aliases.reason is not None:
        return None, aliases.reason
    sid = {r.horse_number: aliases.stored_id(_hid(r.blood_reg_no, race_year) or "?") for r in rows}
    return sid, None


def _repair_odds(session, rep: RepairReport, f: RepairFile, year: int, observed_at, job_id):
    for rid, rows in sorted(_rows_by_race(f.decoded.rows, year).items()):
        rep.races_seen += 1
        with session.begin_nested():
            snap = lock_race(session, rid)
            if snap.race is None or not snap.horses:
                rep.bump("race_not_in_db")
                continue
            if not _netkeiba_era(snap.race.race_date):
                rep.bump("pre_netkeiba_era")
                continue
            sid, reason = _sed_aliases(session, snap, rows, rows[0].race_date.year)
            if sid is None:
                rep.bump(reason)
                continue
            fo = apply_final_odds(
                session,
                rid,
                [
                    ScrapedResultRow(
                        netkeiba_horse_id=sid[r.horse_number],
                        finish_order=r.finish_order,
                        result_status="finished",
                        finish_time=None,
                        win_odds=float(r.final_win_odds) if r.final_win_odds is not None else None,
                        popularity=r.final_win_popularity or None,
                    )
                    for r in rows
                    if r.abnormal_code not in (1, 2)
                ],
                observed_at=observed_at,
                horse_id_of=lambda row: row.netkeiba_horse_id,
            )
            rep.rows_changed += fo.updated
            rep.rows_unchanged += fo.unchanged
            for k in ("unreadable", "missing", "unmatched"):
                if getattr(fo, k):
                    rep.bump(f"final_odds_{k}", getattr(fo, k))


def _repair_weight(session, rep, f, year, observed_at, job_id):
    for rid, rows in sorted(_rows_by_race(f.decoded.rows, year).items()):
        rep.races_seen += 1
        with session.begin_nested():
            snap = lock_race(session, rid)
            if snap.race is None or not snap.horses:
                rep.bump("race_not_in_db")
                continue
            if not _netkeiba_era(snap.race.race_date):
                rep.bump("pre_netkeiba_era")
                continue
            sid, reason = _sed_aliases(session, snap, rows, rows[0].race_date.year)
            if sid is None:
                rep.bump(reason)
                continue
            for r in rows:
                if r.weight is None and r.weight_diff is None:
                    continue
                rh = snap.horses.get(sid[r.horse_number])
                if rh is None:
                    continue
                if (rh.weight, rh.weight_diff) == (r.weight, r.weight_diff):
                    rep.rows_unchanged += 1
                    continue
                ctx = P.TransitionContext(
                    JRDB,
                    observed_at,
                    P.PAGE_RESULTS,
                    snap.prov.horse_source(rh.horse_id, P.GROUP_BODY_WEIGHT),
                    snap.prov.horse_observed_at(rh.horse_id, P.GROUP_BODY_WEIGHT),
                )
                t = P.field_transition(
                    P.GROUP_BODY_WEIGHT, (rh.weight, rh.weight_diff), (r.weight, r.weight_diff), ctx
                )
                if t.decision != "write":
                    rep.bump("kept_by_precedence")
                    continue
                check_payload(
                    CURRENT, "race_horses", {"weight": r.weight, "weight_diff": r.weight_diff}
                )
                session.execute(
                    update(RaceHorse)
                    .where(RaceHorse.race_id == rid, RaceHorse.horse_id == rh.horse_id)
                    .values(weight=r.weight, weight_diff=r.weight_diff)
                )
                write_horse_provenance(
                    session,
                    rid,
                    rh.horse_id,
                    P.GROUP_BODY_WEIGHT,
                    source=JRDB,
                    observed_at=observed_at,
                    job_id=job_id,
                )
                rep.rows_changed += 1


def _repair_last_3f(session, rep, f, year, observed_at, job_id):
    for rid, rows in sorted(_rows_by_race(f.decoded.rows, year).items()):
        rep.races_seen += 1
        with session.begin_nested():
            snap = lock_race(session, rid)
            if snap.race is None or not snap.horses:
                rep.bump("race_not_in_db")
                continue
            sid, reason = _sed_aliases(session, snap, rows, rows[0].race_date.year)
            if sid is None:
                rep.bump(reason)
                continue
            for r in rows:
                if r.last_3f is None:
                    continue
                hid = sid[r.horse_number]
                stored = session.execute(
                    select(RaceResult.last_3f).where(
                        RaceResult.race_id == rid, RaceResult.horse_id == hid
                    )
                ).scalar_one_or_none()
                if stored is None and not session.get(RaceResult, (rid, hid)):
                    rep.bump("result_row_missing")
                    continue
                if stored is not None:
                    rep.rows_unchanged += 1  # fill NULL only (the DB's own value stays)
                    continue
                check_payload(CURRENT, "race_results", {"last_3f": r.last_3f})
                session.execute(
                    update(RaceResult)
                    .where(
                        RaceResult.race_id == rid,
                        RaceResult.horse_id == hid,
                        RaceResult.last_3f.is_(None),
                    )
                    .values(last_3f=float(r.last_3f))
                )
                rep.rows_changed += 1


def _repair_horse_name_column(session, rep, f, year, observed_at, job_id, column: str):
    """owner_name / breeder_name from UKC: fill NULL; a netkeiba-owned master takes JRDB's
    spelling."""

    for h in f.decoded.rows:
        value = getattr(h, column)
        if value is None:
            continue
        hid = _hid(h.blood_reg_no, year, h.birth_date)
        if hid is None:
            rep.bump("unresolvable_blood_no")
            continue
        for candidate in (hid, f"{A.SURROGATE_PREFIX}{hid}"):
            master = session.get(Horse, candidate)
            if master is None:
                continue
            rep.races_seen += 1  # masters seen (the unit of this column)
            stored = getattr(master, column)
            if stored == value:
                rep.rows_unchanged += 1
                continue
            if stored is not None and master.data_source != Source.NETKEIBA:
                rep.bump("kept_non_netkeiba_master")
                continue
            if stored is not None and nz.norm_name(stored, strip_shi=True) != nz.norm_name(
                value, strip_shi=True
            ):
                # codex D23 #3: not a spelling variant but a DIFFERENT value. An owner can change
                # hands, and a UKC snapshot is not evidence that netkeiba's value is the stale
                # one — keep it, report it, let the operator decide.
                rep.bump("netkeiba_value_differs")
                continue
            check_payload(CURRENT, "horses", {column: value})
            # conditional on the value we read (codex D23 #4): a concurrent writer's adoption is
            # never silently overwritten
            res = session.execute(
                update(Horse)
                .where(Horse.horse_id == candidate)
                .where(getattr(Horse, column).is_not_distinct_from(stored))
                .values(**{column: value})
            )
            if res.rowcount:
                rep.rows_changed += 1
            else:
                rep.bump("changed_concurrently")


def _repair_race_column(session, rep, f, year, observed_at, job_id, column: str):
    """race_class / prize_money / post_time from BAC."""

    for p in f.decoded.rows:
        rid = _rid(p.key, year)
        if rid is None:
            continue
        if column == "race_class":
            value = RACE_CLASS_DB.get(p.joken_code or "")
        elif column == "prize_money":
            value = p.prizes_man[0]
        else:
            value = (
                datetime.datetime.combine(p.race_date, p.post_time, tzinfo=JST)
                if p.post_time
                else None
            )
        if value is None:
            rep.bump("no_value_in_file")
            continue
        rep.races_seen += 1
        with session.begin_nested():
            snap = lock_race(session, rid)
            race = snap.race
            if race is None:
                rep.bump("race_not_in_db")
                continue
            stored = getattr(race, column)
            if stored == value:
                rep.rows_unchanged += 1
                continue
            group = P.GROUP_POST_TIME if column == "post_time" else P.GROUP_RACE_STATIC
            if _netkeiba_era(race.race_date):
                ctx = P.TransitionContext(
                    JRDB, observed_at, P.PAGE_ENTRIES, snap.prov.race_source(group), None
                )
                t = P.field_transition(group, (stored,), (value,), ctx)
                if t.decision != "write":
                    rep.bump("kept_by_precedence")
                    if stored is not None:
                        rep.bump("static_mismatch")
                    continue
                if stored is not None:
                    rep.bump("static_mismatch")  # overwritten (netkeiba-owned), reported as 176
            elif stored is not None:
                rep.bump("kept_jra_van")  # JRA-VAN era: fill NULL only
                continue
            check_payload(CURRENT, "races", {column: value})
            res = session.execute(
                update(Race)
                .where(Race.race_id == rid)
                .where(getattr(Race, column).is_not_distinct_from(stored))
                .values(**{column: value})
            )
            if not res.rowcount:
                rep.bump("changed_concurrently")
                continue
            # codex D23 #2: the provenance is per GROUP, this repair is per column. Writing
            # ``jrdb`` here makes netkeiba defer on the whole group (the contract's order anyway:
            # jrdb > netkeiba) — but it must never demote an official-owned group, and a JRA-VAN
            # era NULL fill does not take over a group someone else owns.
            prior = snap.prov.race_source(group)
            if prior in (None, Source.NETKEIBA):
                write_race_provenance(
                    session, rid, group, source=JRDB, observed_at=observed_at, job_id=job_id
                )
            else:
                rep.bump("provenance_kept")
            rep.rows_changed += 1


def _repair_win_payouts(session, rep, f, year, observed_at, job_id):
    for p in f.decoded.rows:
        rid = _rid(p.key, year)
        if rid is None or not p.win:
            continue
        rep.races_seen += 1
        with session.begin_nested():
            snap = lock_race(session, rid)
            if snap.race is None:
                rep.bump("race_not_in_db")
                continue
            if not _netkeiba_era(snap.race.race_date):
                rep.bump("pre_netkeiba_era")
                continue
            check_payload(
                CURRENT,
                "official_win_payouts",
                {"source": OFFICIAL_WIN_PAYOUT_SOURCE_JRDB, "observed_at": observed_at},
            )
            out = upsert_official_win_payouts(
                session,
                rid,
                [ScrapedWinPayout(n, yen) for n, yen in p.win],
                observed_at=observed_at,
                html_sha256=f.meta.sha256,
                source=OFFICIAL_WIN_PAYOUT_SOURCE_JRDB,
                job_id=job_id,
            )
            rep.rows_changed += out.inserted + out.updated + out.removed
            rep.rows_unchanged += out.unchanged
            if out.confirmed:
                rep.bump("win_payout_confirmed", out.confirmed)
            if out.skipped_official:
                rep.bump("win_payout_skipped_official")


_PASS = {
    "odds": _repair_odds,
    "popularity": _repair_odds,
    "weight": _repair_weight,
    "last_3f": _repair_last_3f,
    "win_payouts": _repair_win_payouts,
}


def repair_column(
    session: Session,
    column: str,
    date_from: datetime.date,
    date_to: datetime.date,
    *,
    files_dir: Path,
    parity_dir: Path | None,
    dry_run: bool,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
) -> RepairReport:
    """One column over a date range, through the rules above. The caller owns the transaction:
    commit for a real run, roll back for ``dry_run`` (the report is complete either way)."""

    rep = RepairReport(column, date_from, date_to, dry_run)
    rep.parity_rates = assert_repairable(column, parity_dir)
    spec = REPAIRABLE_COLUMNS[column]
    rep.columns_written = WRITES[column]
    files, errors = load_archive_files(files_dir, spec.kinds, date_from, date_to)
    rep.errors.extend(errors)
    rep.files = [f.summary() for f in files]
    rep.by_year_fill_before = fill_by_year(session, column, date_from, date_to)
    if not files:
        rep.notes.append(
            f"no {'/'.join(spec.kinds)} file for {date_from}..{date_to} under {files_dir}"
        )
    for f in files:
        year = f.meta.data_date.year
        obs = observed_at or f.fetched_at
        try:
            if column in _PASS:
                _PASS[column](session, rep, f, year, obs, job_id)
            elif spec.table == "horses":
                _repair_horse_name_column(session, rep, f, year, obs, job_id, column)
            else:
                _repair_race_column(session, rep, f, year, obs, job_id, column)
        except Exception as exc:  # noqa: BLE001 — one file never aborts the others
            rep.errors.append(f"{f.path.name}: {type(exc).__name__}: {exc}")
    session.flush()
    rep.by_year_fill_after = fill_by_year(session, column, date_from, date_to)
    return rep


def write_report(report: RepairReport, out_dir: Path, run_stamp: str | None = None) -> Path:
    stamp = run_stamp or datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = "-dryrun" if report.dry_run else ""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report.column}-{stamp}{suffix}.json"
    path.write_text(
        json.dumps(report.to_json(), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def total_fill(by_year: dict) -> float | None:
    n = sum(v["n"] for v in by_year.values())
    filled = sum(v["filled"] for v in by_year.values())
    return (filled / n) if n else None


__all__ = [
    "REFUSED_COLUMNS",
    "REPAIRABLE_COLUMNS",
    "ColumnSpec",
    "RepairColumnRefused",
    "RepairFile",
    "RepairReport",
    "assert_repairable",
    "fill_by_year",
    "load_archive_files",
    "repair_column",
    "total_fill",
    "write_report",
]
