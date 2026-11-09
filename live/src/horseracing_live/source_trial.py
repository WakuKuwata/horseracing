"""Feature 178 US3.5 — the 運用成立試験 in an ISOLATED copy (T063/T064; FR-015/015a・SC-001a).

Question: with netkeiba supplying NOTHING for the day (no rows, no masters, no values), do JRDB
files replayed at their arrival times plus the JRA official route produce, for EVERY race of the
day, the entry rows → the T−3 decision record → results → final win odds → payouts — and does the
emergency mode recover when the JRDB 前日 file is late?

How: a replica DB has the day's rows removed (``reset_day``: the day's races, entries, results,
payouts, laps, provenance, quotes, file rows — NOT a snapshot restore; what is inherited from
before the day is recorded as counts and hashes in the frozen config), the isolation is checked,
then an event loop replays each JRDB file at its arrival time through the SAME writer entry
(``cli_write.run_write``), captures the T−3 quote from the official 単複 page through the
production capture (``near_post.capture_race(quote_source='jra_official')`` — one gate order, one
lock, one set of rechecks for production and trial), and scores each race at trial end. Records are
append-only JSONL.

Two replay modes are recorded, never mixed: ``realtime`` (the CLI: real clock, real official pages,
R8) and ``fake_clock`` (tests: order and gates only — a fake clock cannot make the official site
answer with that moment's page, codex D24 Q1-1).

Outbound connections of this process are limited to the allowed hosts (the JRA site); anything else
— netkeiba above all — fails at the socket (``block_outbound_except``). A fetch that silently returns
nothing still fails the trial: every expected race must have rows, a quote and its values.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import socket
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from . import near_post as np_
from .near_post import DayFeatures, NearPostPredictor, NearPostReport, Scheduled, schedule_day
from .official_quote import fetch_official_quote, make_quote_fetcher, official_quote

JST = datetime.timezone(datetime.timedelta(hours=9))
UTC = datetime.UTC
_REPO = Path(__file__).resolve().parents[3]
DEFAULT_TRIAL_DIR = _REPO / "artifacts" / "jrdb" / "trial"
SOURCE_OFFICIAL = np_.SOURCE_OFFICIAL
TRIAL_VERSION = "source_trial_v2"
LOGIC_VERSION = np_.LOGIC_VERSION + f";src={SOURCE_OFFICIAL}"
ALLOWED_HOSTS: tuple[str, ...] = ("www.jra.go.jp",)
BLOCKED_HOSTS: tuple[str, ...] = ("netkeiba.com", "race.netkeiba.com", "db.netkeiba.com")
STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("entries", ("BAC", "KYI", "UKC")),
    ("prerace", ("TYB",)),
    ("results", ("SED", "SRB")),
    ("payouts", ("HJC",)),
)
KIND_STEP = {k: step for step, kinds in STEPS for k in kinds}
#: the files the trial registers as the 正式版 (an archive copy fed at the formal arrival time)
ARCHIVE_VARIANT_KINDS = frozenset({"SED", "SRB"})
EMERGENCY_DEFAULT_TIME = datetime.time(8, 0)
REPLAY_MODES = ("realtime", "fake_clock")


def _now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


# --------------------------------------------------------------------------- arrivals


def default_arrivals(day: datetime.date) -> dict[str, datetime.datetime]:
    """The schedule the member site keeps (research D16): 前日 files the evening before, TYB in
    the morning, HJC in the evening, SED/SRB 正式版 on the following Thursday. An ASSUMED
    schedule — a trial run on it is recorded as such (``arrivals_basis='default'``)."""

    prev = day - datetime.timedelta(days=1)
    thursday = day + datetime.timedelta(days=(3 - day.weekday()) % 7 or 7)
    at = lambda d, h, m=0: datetime.datetime.combine(d, datetime.time(h, m), tzinfo=JST)  # noqa: E731
    return {
        "BAC": at(prev, 20),
        "KYI": at(prev, 20),
        "UKC": at(prev, 20),
        "TYB": at(day, 9),
        "HJC": at(day, 18, 30),
        "SED": at(thursday, 16),
        "SRB": at(thursday, 16),
    }


def arrivals_from_probe(path: Path, day: datetime.date) -> dict[str, datetime.datetime]:
    """The FIRST ``present`` observation per kind of THIS day's files in a 段 0 ``arrivals`` JSONL
    (records are matched by ``source_url_sha256`` of the day's member URLs, so another day's or
    another version's file never counts; codex D24 Q1-4). The observation time is the conservative
    arrival (``last_modified`` is kept as information only). ``SED_sokuho`` is not an input."""

    from horseracing_scrape.jrdb import fetch as jrdb_fetch

    from . import source_probe as sp

    by_sha = {
        jrdb_fetch.source_url_sha256(url): label for label, url in sp.arrival_targets(day).items()
    }
    out: dict[str, datetime.datetime] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("mode") != "arrivals" or rec.get("status") != "present":
            continue
        label = by_sha.get(str(rec.get("source_url_sha256", "")))
        if label is None or label == "SED_sokuho":
            continue
        kind = "TYB" if label.startswith("TYB") else label
        if kind not in KIND_STEP:
            continue
        when = datetime.datetime.fromisoformat(rec["observed_at"])
        if kind not in out or when < out[kind]:
            out[kind] = when
    return out


def apply_delay(
    arrivals: Mapping[str, datetime.datetime], delay: Mapping[str, datetime.timedelta] | None
) -> dict[str, datetime.datetime]:
    out = dict(arrivals)
    for kind, by in (delay or {}).items():
        if kind in out:
            out[kind] = out[kind] + by
    return out


def parse_duration(text_: str) -> datetime.timedelta:
    """``2h`` / ``30m`` / ``90s`` / ``1d``."""

    s = text_.strip().lower()
    unit, n = s[-1], float(s[:-1])
    if unit == "h":
        return datetime.timedelta(hours=n)
    if unit == "m":
        return datetime.timedelta(minutes=n)
    if unit == "s":
        return datetime.timedelta(seconds=n)
    if unit == "d":
        return datetime.timedelta(days=n)
    raise ValueError(f"duration must end in d/h/m/s: {text_!r}")


# --------------------------------------------------------------------------- config (frozen)


@dataclass
class TrialConfig:
    day: str
    files_dir: str
    replay_mode: str
    arrivals_basis: str
    arrivals: dict[str, str]
    delay_s: dict[str, float]
    emergency_at: str
    allowed_hosts: tuple[str, ...]
    blocked_hosts: tuple[str, ...]
    #: what the replica may inherit from before the day (codex D18 #6 / D24 Q1-2): everything of
    #: PRIOR days and the person mapping as of R3 — with their counts and a hash, taken at start
    inherited: dict[str, Any]
    inherited_state: dict[str, Any]
    file_sha256: dict[str, str]
    trial_version: str = TRIAL_VERSION
    logic_version: str = LOGIC_VERSION
    frozen_at: str | None = None


def inherited_state(session: Session, day: datetime.date) -> dict[str, Any]:
    """Counts and a hash of what the trial inherits (fixes the pre-trial state, Q1-2)."""

    mapped = session.execute(
        text(
            "SELECT entity_type, source_id, canonical_id FROM id_mappings "
            "WHERE source = 'jrdb' AND mapping_status = 'mapped' ORDER BY 1, 2"
        )
    ).all()
    sha = hashlib.sha256(json.dumps([list(r) for r in mapped]).encode()).hexdigest()
    by_source = dict(
        session.execute(
            text("SELECT coalesce(data_source, 'null'), count(*) FROM horses GROUP BY 1 ORDER BY 1")
        ).all()
    )
    prior_races = int(
        session.execute(
            text("SELECT count(*) FROM races WHERE race_date < :d"), {"d": day}
        ).scalar_one()
    )
    return {
        "jrdb_person_mappings": {"n": len(mapped), "sha256": sha},
        "horses_by_data_source": {k: int(v) for k, v in by_source.items()},
        "races_before_day": prior_races,
    }


def build_config(
    day: datetime.date,
    *,
    files_dir: Path,
    arrivals: Mapping[str, datetime.datetime],
    arrivals_basis: str,
    delay: Mapping[str, datetime.timedelta] | None,
    emergency_at: datetime.datetime,
    replay_mode: str,
    inherited_state_: dict[str, Any] | None = None,
    file_sha256: Mapping[str, str] | None = None,
) -> TrialConfig:
    if replay_mode not in REPLAY_MODES:
        raise ValueError(f"replay_mode must be one of {REPLAY_MODES}")
    return TrialConfig(
        day=day.isoformat(),
        files_dir=str(files_dir),
        replay_mode=replay_mode,
        arrivals_basis=arrivals_basis,
        arrivals={k: v.isoformat() for k, v in sorted(arrivals.items())},
        delay_s={k: v.total_seconds() for k, v in (delay or {}).items()},
        emergency_at=emergency_at.isoformat(),
        allowed_hosts=ALLOWED_HOSTS,
        blocked_hosts=BLOCKED_HOSTS,
        inherited={
            "prior_days": "races / masters / results / provenance of days before the trial day",
            "person_mapping": "id_mappings(source=jrdb, mapped) as of R3",
            "trial_day": "nothing: the day's rows are removed before the replay (reset_day)",
        },
        inherited_state=dict(inherited_state_ or {}),
        file_sha256=dict(file_sha256 or {}),
    )


def freeze_config(cfg: TrialConfig, path: Path) -> Path:
    if path.exists():
        raise FileExistsError(f"trial config exists (append-only): {path}")
    cfg.frozen_at = _now().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def append_record(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# --------------------------------------------------------------------------- isolation


_ISOLATION_SQL: dict[str, str] = {
    "nk_rows_today": """
        SELECT count(*) FROM race_horses rh JOIN races r USING (race_id)
        WHERE r.race_date = :day AND rh.horse_id LIKE 'nk:%'""",
    # masters CREATED during the day: an inherited netkeiba master of an earlier day is allowed
    "netkeiba_masters_created_today": """
        SELECT count(*) FROM horses h
        WHERE h.data_source = 'netkeiba' AND h.created_at >= :day_start""",
    "netkeiba_race_provenance_today": """
        SELECT count(*) FROM race_field_provenance p JOIN races r USING (race_id)
        WHERE r.race_date = :day AND p.source = 'netkeiba'""",
    "netkeiba_horse_provenance_today": """
        SELECT count(*) FROM race_horse_field_provenance p JOIN races r USING (race_id)
        WHERE r.race_date = :day AND p.source = 'netkeiba'""",
    "netkeiba_mappings_touched_today": """
        SELECT count(*) FROM id_mappings
        WHERE source = 'netkeiba' AND updated_at >= :day_start""",
    "netkeiba_jobs_today": """
        SELECT count(*) FROM ingestion_jobs
        WHERE source = 'netkeiba' AND created_at >= :day_start""",
}


def check_isolation(
    session: Session, day: datetime.date, *, cache_dir: Path | None = None
) -> list[str]:
    """Violations of 'netkeiba supplies nothing for the day' (empty = isolated)."""

    day_start = datetime.datetime.combine(day, datetime.time(0, 0), tzinfo=JST)
    out: list[str] = []
    for key, sql in _ISOLATION_SQL.items():
        n = int(session.execute(text(sql), {"day": day, "day_start": day_start}).scalar_one())
        if n:
            out.append(f"{key}={n}")
    if cache_dir is not None and cache_dir.exists() and any(cache_dir.iterdir()):
        out.append(f"http_cache_not_empty={cache_dir}")
    return out


_RESET_SQL: tuple[str, ...] = (
    "DELETE FROM decision_quotes WHERE race_id IN (SELECT race_id FROM races WHERE race_date = :d)",
    "DELETE FROM race_horse_field_provenance WHERE race_id IN "
    "(SELECT race_id FROM races WHERE race_date = :d)",
    "DELETE FROM race_field_provenance WHERE race_id IN "
    "(SELECT race_id FROM races WHERE race_date = :d)",
    "DELETE FROM exotic_odds WHERE race_id IN (SELECT race_id FROM races WHERE race_date = :d)",
    "DELETE FROM official_win_payouts WHERE race_id IN "
    "(SELECT race_id FROM races WHERE race_date = :d)",
    "DELETE FROM race_laps WHERE race_id IN (SELECT race_id FROM races WHERE race_date = :d)",
    "DELETE FROM race_results WHERE race_id IN (SELECT race_id FROM races WHERE race_date = :d)",
    "DELETE FROM race_horses WHERE race_id IN (SELECT race_id FROM races WHERE race_date = :d)",
    "DELETE FROM races WHERE race_date = :d",
    "DELETE FROM jrdb_files WHERE data_date = :d",
)


def reset_day(session: Session, day: datetime.date) -> dict[str, int]:
    """Remove everything the day produced (append-only triggers bypassed with the replica role —
    this is a COPY, never the production DB). Not a snapshot restore: see ``inherited_state``."""

    counts: dict[str, int] = {}
    session.execute(text("SET LOCAL session_replication_role = 'replica'"))
    for sql in _RESET_SQL:
        table = sql.split(" FROM ")[1].split(" ")[0]
        counts[table] = int(session.execute(text(sql), {"d": day}).rowcount or 0)
    return counts


# --------------------------------------------------------------------------- outbound block


class OutboundBlocked(RuntimeError):
    pass


def block_outbound_except(
    allowed_hosts: Iterable[str] = ALLOWED_HOSTS,
    *,
    resolve: Callable[[str], set[str]] | None = None,
):
    """A context manager: Python-level sockets may connect only to the allowed hosts' addresses
    (resolved once at entry) and loopback. Native libpq connections are unaffected (172 pattern).
    This is one layer, not the proof: the trial's verdict rests on the inputs every race shows."""

    import contextlib

    def _resolve(host: str) -> set[str]:
        try:
            return {info[4][0] for info in socket.getaddrinfo(host, None)}
        except OSError:
            return set()

    resolver = resolve or _resolve
    allowed_ips: set[str] = set()
    for host in allowed_hosts:
        allowed_ips |= resolver(host)
    allowed_ips |= {"127.0.0.1", "::1"}

    @contextlib.contextmanager
    def _cm():
        saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection

        def _check(address):
            host = address[0] if isinstance(address, tuple) else address
            if str(host) not in allowed_ips:
                raise OutboundBlocked(f"outbound connection blocked by the trial: {host}")

        def connect(self, address, *a, **k):
            _check(address)
            return saved[0](self, address, *a, **k)

        def connect_ex(self, address, *a, **k):
            _check(address)
            return saved[1](self, address, *a, **k)

        def create_connection(address, *a, **k):
            _check(address)
            return saved[2](address, *a, **k)

        socket.socket.connect = connect  # type: ignore[method-assign]
        socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
        socket.create_connection = create_connection  # type: ignore[assignment]
        try:
            yield allowed_ips
        finally:
            socket.socket.connect, socket.socket.connect_ex, socket.create_connection = saved  # type: ignore[method-assign]

    return _cm()


# --------------------------------------------------------------------------- the T−3 capture (official)


def capture_official(
    session: Session,
    *,
    scheduled: Scheduled,
    fetcher,
    predictor: NearPostPredictor,
    day_features: DayFeatures,
    run_id: uuid.UUID,
    clock: Callable[[], datetime.datetime] = _now,
    monotonic: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
    walker_factory=None,
    parse=None,
) -> NearPostReport:
    """The production capture with the official source (codex D24 Q1-8: one gate order, one
    advisory lock, one set of rechecks for production and trial; the fake walker is injected)."""

    return np_.capture_race(
        session,
        scheduled=scheduled,
        fetcher=fetcher,
        predictor=predictor,
        day_features=day_features,
        run_id=run_id,
        clock=clock,
        monotonic=monotonic,
        deadline=deadline,
        quote_source=SOURCE_OFFICIAL,
        quote_fetcher=make_quote_fetcher(fetcher, walker_factory=walker_factory, parse=parse),
    )


# --------------------------------------------------------------------------- JRDB arrivals → writers


def files_present(files_dir: Path, day: datetime.date) -> dict[str, list[Path]]:
    """``files_dir/<KIND>/<yyyymmdd>/*.txt`` per kind (the ``artifacts/jrdb`` layout)."""

    out: dict[str, list[Path]] = {}
    for kind in KIND_STEP:
        folder = Path(files_dir) / kind / day.strftime("%Y%m%d")
        if folder.is_dir():
            paths = sorted(folder.glob("*.txt"))
            if paths:
                out[kind] = paths
    return out


def register_arrivals(
    session: Session, paths: Iterable[Path], kind: str, day: datetime.date, *, arrived_at
) -> int:
    """The arriving file becomes a ``jrdb_files`` row (``archive`` for SED/SRB = the 正式版)."""

    from horseracing_scrape.jrdb import files as jfiles

    n = 0
    for path in paths:
        raw = path.read_bytes()
        meta = jfiles.build_meta(
            path.name,
            raw,
            variant="archive" if kind in ARCHIVE_VARIANT_KINDS else "daily",
            fetched_at=arrived_at,
            data_date=day,
        )
        _row, created = jfiles.record_fetched(
            session,
            meta,
            source_url_sha256="trial:" + path.name,
            fetched_at=arrived_at,
            published_at=arrived_at,
        )
        n += int(created)
    return n


def ingest_step(
    session_factory, step: str, day: datetime.date, *, files_dir: Path, policy_path: Path
) -> dict:
    """One JRDB step through the production writer command (``cli_write.run_write``)."""

    from horseracing_scrape.jrdb.cli_write import run_write

    args = SimpleNamespace(
        command=f"jrdb-{step}",
        date=day.isoformat(),
        profile="current",
        policy=str(policy_path),
        database_url=None,
        venue=None,
        json=True,
    )
    result = run_write(
        args, session_factory=session_factory, artifacts=Path(files_dir), production_url=None
    )
    return result.to_json()


def expected_races_from_bac(paths: Iterable[Path], day: datetime.date) -> set[str]:
    """The day's race ids the JRDB 番組 (BAC) lists — the expected set the generated rows are
    checked against (codex D24 Q1-7: a race that never got rows must not vanish from the count)."""

    from horseracing_scrape.jrdb import files as jfiles
    from horseracing_scrape.jrdb.decode import decode_file
    from horseracing_scrape.jrdb.ids import race_id_from_key

    out: set[str] = set()
    for path in paths:
        raw = path.read_bytes()
        meta = jfiles.build_meta(path.name, raw, variant="daily", fetched_at=_now(), data_date=day)
        for p in decode_file(raw, meta, profile="current").rows:
            try:
                out.add(race_id_from_key(p.key, file_year=day.year))
            except Exception:  # noqa: BLE001 — a non-JRA key is not an expected race
                continue
    return out


# --------------------------------------------------------------------------- emergency mode


def official_entries_default(day: datetime.date, fetcher):
    """index → entries day pages + the changes page (the 176 walk)."""

    from horseracing_scrape.jra.models import KIND_CHANGES
    from horseracing_scrape.jra.navigate import CHANGES_CNAME, CHANGES_URL, Walker
    from horseracing_scrape.jra.parse.changes import parse_changes
    from horseracing_scrape.jra.parse.entries import parse_entries_day

    walker = Walker(fetcher, date=day)
    pages = []
    for _link, resp in walker.day_pages("entries"):
        pages.append((resp.fetched_at, parse_entries_day(resp.text, expected_date=day)))
    try:
        changes = parse_changes(
            fetcher.post(CHANGES_URL, CHANGES_CNAME, kind=KIND_CHANGES).text, expected_date=day
        )
    except Exception:  # noqa: BLE001 — changes are optional for the emergency rows
        changes = ()
    return pages, changes


def run_emergency(
    session: Session, day: datetime.date, *, fetcher, observed_at, official_entries=None
) -> dict:
    """``jra_emergency_entries: true`` 相当: rows for every published race whose stored field is
    empty (per race — a day with some JRDB races and some missing is covered too)."""

    from horseracing_scrape.jra.emergency_entries import create_entries_from_official

    loader = official_entries or official_entries_default
    pages, changes = loader(day, fetcher)
    rows = races = 0
    for fetched_at, page in pages:
        c = create_entries_from_official(
            session, page, changes, policy=True, observed_at=fetched_at or observed_at
        )
        rows += int(c.extra.get("emergency_rows_created", 0))
        races += int(c.extra.get("emergency_races_created", 0))
    return {"emergency_rows_created": rows, "emergency_races_created": races, "pages": len(pages)}


# --------------------------------------------------------------------------- records


@dataclass
class RaceRecord:
    race_id: str
    post_time: str | None = None
    deadline: str | None = None
    entries_source: str | None = None
    entries_at: str | None = None
    features_ready_at: str | None = None
    master_source: str | None = None
    masters_created_in_trial_by_source: dict[str, int] = field(default_factory=dict)
    capture_started_at: str | None = None
    quote_at: str | None = None
    fetch_seconds: float | None = None
    quote_ok: bool = False
    quote_reason: str | None = None
    results_at: str | None = None
    final_odds_at: str | None = None
    payouts_at: str | None = None
    deadline_delta_s: float | None = None
    emergency: bool = False
    static_rewritten_by_late_kyi: bool = False
    ok: bool = False
    reason: str | None = None

    def to_json(self) -> dict:
        return {"record": "race", **asdict(self)}


@dataclass
class TrialSummary:
    day: str
    run_id: str
    replay_mode: str = "realtime"
    config_path: str | None = None
    out_path: str | None = None
    reset: dict[str, int] = field(default_factory=dict)
    isolation_violations: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    emergency: dict | None = None
    expected_races: int = 0
    missing_races: list[str] = field(default_factory=list)
    races: list[RaceRecord] = field(default_factory=list)
    n_ok: int = 0
    n_failed: int = 0
    exit_code: int = 0
    error: str | None = None

    def to_json(self) -> dict:
        return {
            "record": "summary",
            "day": self.day,
            "run_id": self.run_id,
            "replay_mode": self.replay_mode,
            "config_path": self.config_path,
            "out_path": self.out_path,
            "reset": self.reset,
            "isolation_violations": self.isolation_violations,
            "events": self.events,
            "emergency": self.emergency,
            "expected_races": self.expected_races,
            "missing_races": self.missing_races,
            "n_races": len(self.races),
            "n_ok": self.n_ok,
            "n_failed": self.n_failed,
            "failed": [r.race_id for r in self.races if not r.ok],
            "exit_code": self.exit_code,
            "error": self.error,
        }


def _source_of(session: Session, race_id: str, group: str) -> str | None:
    return session.execute(
        text("SELECT source FROM race_field_provenance WHERE race_id = :r AND field_group = :g"),
        {"r": race_id, "g": group},
    ).scalar_one_or_none()


def _entry_source(session: Session, race_id: str) -> str | None:
    rows = (
        session.execute(
            text(
                "SELECT DISTINCT source FROM race_horse_field_provenance "
                "WHERE race_id = :r AND field_group = 'entry_static' ORDER BY 1"
            ),
            {"r": race_id},
        )
        .scalars()
        .all()
    )
    return "+".join(rows) if rows else None


def _master_sources(
    session: Session, race_id: str, since: datetime.datetime
) -> tuple[str | None, dict[str, int]]:
    rows = session.execute(
        text(
            "SELECT coalesce(h.data_source, 'unknown'), count(*), "
            "count(*) FILTER (WHERE h.created_at >= :since) "
            "FROM race_horses rh JOIN horses h USING (horse_id) "
            "WHERE rh.race_id = :r GROUP BY 1 ORDER BY 1"
        ),
        {"r": race_id, "since": since},
    ).all()
    all_sources = "+".join(r[0] for r in rows) if rows else None
    created = {r[0]: int(r[2]) for r in rows if r[2]}
    return all_sources, created


def score_race(
    session: Session,
    rec: RaceRecord,
    *,
    quote: NearPostReport | None,
    step_times: Mapping[str, datetime.datetime],
    trial_started_at: datetime.datetime,
) -> RaceRecord:
    """Judge one race at trial end: entries before the deadline, nothing of the day supplied by
    netkeiba (masters created DURING the trial by netkeiba count; inherited masters do not), the
    quote captured, results for the field, every started horse priced with a final (quote-less)
    odds, payouts present."""

    rid = rec.race_id
    rec.entries_source = _entry_source(session, rid)
    rec.master_source, rec.masters_created_in_trial_by_source = _master_sources(
        session, rid, trial_started_at
    )
    if quote is not None:
        rec.quote_ok = quote.status == "captured"
        rec.quote_reason = quote.reason
        rec.quote_at = quote.captured_at.isoformat() if quote.captured_at else None
        if quote.captured_at and rec.capture_started_at:
            rec.fetch_seconds = (
                quote.captured_at - datetime.datetime.fromisoformat(rec.capture_started_at)
            ).total_seconds()
    n_started, n_results, n_priced, quoteless = session.execute(
        text(
            "SELECT count(*) FILTER (WHERE rh.entry_status = 'started'), "
            "(SELECT count(*) FROM race_results rr WHERE rr.race_id = :r), "
            "count(*) FILTER (WHERE rh.entry_status = 'started' AND rh.odds IS NOT NULL), "
            "(SELECT win_odds_official_at IS NULL FROM races WHERE race_id = :r) "
            "FROM race_horses rh WHERE rh.race_id = :r"
        ),
        {"r": rid},
    ).one()
    has_results = n_results > 0
    has_final_odds = n_started > 0 and n_priced == n_started and bool(quoteless)
    has_payouts = bool(
        session.scalar(
            text("SELECT 1 FROM official_win_payouts WHERE race_id = :r LIMIT 1"), {"r": rid}
        )
    )
    results_at = step_times.get("results")
    payouts_at = step_times.get("payouts")
    rec.results_at = results_at.isoformat() if has_results and results_at else None
    rec.final_odds_at = results_at.isoformat() if has_final_odds and results_at else None
    rec.payouts_at = payouts_at.isoformat() if has_payouts and payouts_at else None
    if rec.entries_at and rec.deadline:
        rec.deadline_delta_s = (
            datetime.datetime.fromisoformat(rec.deadline)
            - datetime.datetime.fromisoformat(rec.entries_at)
        ).total_seconds()
    problems = []
    if rec.entries_source is None:
        problems.append("no_entries")
    elif rec.deadline_delta_s is not None and rec.deadline_delta_s <= 0:
        problems.append("entries_after_deadline")
    if "netkeiba" in (rec.entries_source or ""):
        problems.append("netkeiba_supplied_entries")
    if rec.masters_created_in_trial_by_source.get("netkeiba"):
        problems.append("netkeiba_supplied_masters")
    if not rec.quote_ok:
        problems.append(f"quote:{rec.quote_reason or 'missing'}")
    if not has_results:
        problems.append("results_missing")
    if not has_final_odds:
        problems.append("final_odds_missing")
    if not has_payouts:
        problems.append("payouts_missing")
    rec.ok = not problems
    rec.reason = "ok" if rec.ok else ";".join(problems)
    return rec


# --------------------------------------------------------------------------- the trial


def run_trial(
    day: datetime.date,
    *,
    db_url: str,
    files_dir: Path,
    out_path: Path,
    config_path: Path,
    policy_path: Path,
    jra_fetcher,
    predictor: NearPostPredictor,
    arrivals: Mapping[str, datetime.datetime] | None = None,
    arrivals_basis: str | None = None,
    delay: Mapping[str, datetime.timedelta] | None = None,
    emergency_at: datetime.datetime | None = None,
    replay_mode: str = "realtime",
    clock: Callable[[], datetime.datetime] = _now,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    features_builder: Callable[[Session, datetime.date], DayFeatures] | None = None,
    official_entries=None,
    walker_factory=None,
    parse=None,
    cache_dir: Path | None = None,
    end_at: datetime.datetime | None = None,
) -> TrialSummary:
    """Replay one day. Times come from ``clock``/``sleep`` (real in production, fake in tests)."""

    from horseracing_db.session import create_db_engine

    run_id = uuid.uuid4()
    summary = TrialSummary(day=day.isoformat(), run_id=str(run_id), replay_mode=replay_mode)
    basis = arrivals_basis or ("given" if arrivals is not None else "default")
    arrivals_ = apply_delay(arrivals or default_arrivals(day), delay)
    emergency_at = emergency_at or datetime.datetime.combine(
        day, EMERGENCY_DEFAULT_TIME, tzinfo=JST
    )
    if config_path.exists():
        raise FileExistsError(f"trial config exists (append-only): {config_path}")
    engine = create_db_engine(db_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    present = files_present(files_dir, day)
    file_sha = {
        f"{k}/{p.name}": hashlib.sha256(p.read_bytes()).hexdigest()
        for k, ps in present.items()
        for p in ps
    }
    step_times: dict[str, datetime.datetime] = {}
    quotes: dict[str, NearPostReport] = {}
    records: dict[str, RaceRecord] = {}
    entries_at: datetime.datetime | None = None
    schedule: list[Scheduled] = []
    day_features: DayFeatures | None = None
    build = features_builder or (
        lambda s, d: np_.build_day_features(s.connection(), d, clock=clock)
    )
    trial_started_at = clock()
    try:
        with factory() as session:
            summary.reset = reset_day(session, day)
            session.commit()
            state = inherited_state(session, day)
            summary.isolation_violations = check_isolation(session, day, cache_dir=cache_dir)
        cfg = build_config(
            day,
            files_dir=files_dir,
            arrivals=arrivals_,
            arrivals_basis=basis,
            delay=delay,
            emergency_at=emergency_at,
            replay_mode=replay_mode,
            inherited_state_=state,
            file_sha256=file_sha,
        )
        summary.config_path = str(freeze_config(cfg, config_path))
        summary.out_path = str(out_path)
        if summary.isolation_violations:
            summary.exit_code, summary.error = 2, "isolation_violated"
            append_record(
                out_path,
                {
                    "record": "isolation_violated",
                    "day": day.isoformat(),
                    "violations": summary.isolation_violations,
                },
            )
            return summary
        expected: set[str] = set()
        if "BAC" in present:
            expected = expected_races_from_bac(present["BAC"], day)
            summary.expected_races = len(expected)

        events: list[tuple[datetime.datetime, str, str]] = []
        for step, kinds in STEPS:
            kinds_here = [k for k in kinds if k in present]
            if kinds_here:
                when = max(arrivals_[k] for k in kinds_here if k in arrivals_)
                events.append((when, "arrive", step))
        events.append((emergency_at, "emergency_check", ""))
        events.sort(key=lambda e: e[0])

        def _wait_until(t: datetime.datetime) -> None:
            delta = (t - clock()).total_seconds()
            if delta > 0:
                sleep(delta)

        def _after_entries(session: Session, when: datetime.datetime, how: str) -> None:
            nonlocal entries_at, schedule, day_features
            races = np_.day_races(session, day)
            if not races:
                return
            if entries_at is None:
                entries_at = when
            if not schedule:
                schedule = schedule_day(races)
                for s in schedule:
                    records[s.race_id] = RaceRecord(
                        s.race_id,
                        post_time=s.post_time.isoformat(),
                        deadline=s.target_at.isoformat(),
                        entries_at=when.isoformat(),
                        emergency=(how == "emergency"),
                    )
                    events.append((s.window_open, "capture", s.race_id))
                events.sort(key=lambda e: e[0])
                day_features = build(session, day)
                session.rollback()
                ready = clock().isoformat()
                for r in records.values():
                    r.features_ready_at = ready

        i = 0
        while i < len(events):
            when, kind, what = events[i]
            i += 1
            _wait_until(when)
            now = clock()
            ev: dict[str, Any] = {
                "at": now.isoformat(),
                "scheduled": when.isoformat(),
                "event": kind,
                "what": what,
            }
            if kind == "arrive":
                step = what
                with factory() as session:
                    for k in dict(STEPS)[step]:
                        if k in present:
                            register_arrivals(session, present[k], k, day, arrived_at=now)
                    session.commit()
                res = ingest_step(factory, step, day, files_dir=files_dir, policy_path=policy_path)
                step_times[step] = now
                ev["result"] = {
                    k: v for k, v in res.items() if k in ("jobs", "notes", "exit_code", "error")
                }
                with factory() as session:
                    if step == "entries":
                        if entries_at is not None:  # rows existed already (emergency): KYI is late
                            ev["late_kyi_static_mismatch_reported"] = sum(
                                int(j.get("static_mismatch", 0)) for j in res.get("jobs", [])
                            )
                            for r in records.values():
                                if r.emergency:  # the official static must have survived (T064)
                                    r.static_rewritten_by_late_kyi = (
                                        _source_of(session, r.race_id, "race_static")
                                        != SOURCE_OFFICIAL
                                    )
                        _after_entries(session, now, "jrdb")
            elif kind == "emergency_check":
                with factory() as session:
                    if "entries" in step_times:
                        ev["result"] = "jrdb_entries_present"
                    else:  # the JRDB 前日 file has not arrived: the official 出馬表 creates the field
                        try:
                            summary.emergency = run_emergency(
                                session,
                                day,
                                fetcher=jra_fetcher,
                                observed_at=now,
                                official_entries=official_entries,
                            )
                            session.commit()
                            ev["result"] = summary.emergency
                            _after_entries(session, now, "emergency")
                        except Exception as exc:  # noqa: BLE001
                            session.rollback()
                            ev["result"] = f"emergency_failed:{type(exc).__name__}:{exc}"
            elif kind == "capture":
                s = next(x for x in schedule if x.race_id == what)
                records[what].capture_started_at = now.isoformat()
                with factory() as session:
                    try:
                        report = capture_official(
                            session,
                            scheduled=s,
                            fetcher=jra_fetcher,
                            predictor=predictor,
                            day_features=day_features,
                            run_id=run_id,
                            clock=clock,
                            monotonic=monotonic,
                            walker_factory=walker_factory,
                            parse=parse,
                        )
                        if report.changed:
                            session.commit()
                        else:
                            session.rollback()
                    except Exception as exc:  # noqa: BLE001
                        session.rollback()
                        report = NearPostReport(
                            what, "failed", f"error:{type(exc).__name__}", s.capture_target
                        )
                quotes[what] = report
                ev["result"] = {"status": report.status, "reason": report.reason}
            summary.events.append(ev)
            append_record(
                out_path, {"record": "event", "day": day.isoformat(), "run_id": str(run_id), **ev}
            )
        if end_at is not None:
            _wait_until(end_at)
        with factory() as session:
            generated = {
                r
                for (r,) in session.execute(
                    text("SELECT race_id FROM races WHERE race_date = :d"), {"d": day}
                )
            }
            summary.missing_races = sorted(expected - generated)
            for rid, rec in sorted(records.items()):
                score_race(
                    session,
                    rec,
                    quote=quotes.get(rid),
                    step_times=step_times,
                    trial_started_at=trial_started_at,
                )
                summary.races.append(rec)
                append_record(
                    out_path, {"day": day.isoformat(), "run_id": str(run_id), **rec.to_json()}
                )
            session.rollback()
        summary.n_ok = sum(1 for r in summary.races if r.ok)
        summary.n_failed = len(summary.races) - summary.n_ok
        if not summary.races:
            summary.exit_code, summary.error = 1, "no_races"
        elif summary.n_failed or summary.missing_races:
            summary.exit_code = 1
            summary.error = f"{summary.n_failed} race(s) not satisfied" + (
                f"; {len(summary.missing_races)} expected race(s) never got rows"
                if summary.missing_races
                else ""
            )
        append_record(out_path, summary.to_json())
    finally:
        engine.dispose()
    return summary


__all__ = [
    "ALLOWED_HOSTS",
    "BLOCKED_HOSTS",
    "LOGIC_VERSION",
    "REPLAY_MODES",
    "STEPS",
    "OutboundBlocked",
    "RaceRecord",
    "TrialConfig",
    "TrialSummary",
    "apply_delay",
    "arrivals_from_probe",
    "block_outbound_except",
    "build_config",
    "capture_official",
    "check_isolation",
    "default_arrivals",
    "expected_races_from_bac",
    "fetch_official_quote",
    "files_present",
    "freeze_config",
    "ingest_step",
    "inherited_state",
    "official_quote",
    "parse_duration",
    "register_arrivals",
    "reset_day",
    "run_emergency",
    "run_trial",
    "score_race",
]
