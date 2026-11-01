"""Feature 174: the near-post (T−3 min) decision record.

One manually started process per race day: build the day's as-of features once in the morning,
then for each race, inside ``[post_time − 240 s, post_time − 120 s]`` (``target = −180 s``; races
sharing a post time step back 60 s each in race_id order), fetch the win/place quote ONCE through
the shared polite fetcher, recompute the market block with the production ``market_structure``,
predict with the production market-EV models (15-seed ensemble, single seed, 141 mixture) and
freeze everything in ``decision_quotes`` (one row per race, append-only, constitution V 1.0.1 note).

Fail-closed gates, in order (fetch is last): policy date → robots freshness → race still
result-pending → post_time known and in the future → inside the window → entries complete →
no existing row → morning feature rows match the started field → advisory lock → fetch →
post-fetch rechecks (pending, field unchanged, quote covers the field) → predict → save.
``dry_run`` stops right before the fetch and performs no external request.

Nothing here feeds back into model features (constitution II); settlement is not stored.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus
from horseracing_db.models import DecisionQuote, Race, RaceHorse
from horseracing_eval import market_mix
from horseracing_scrape import robots_cache
from horseracing_scrape.fetch import FetchError, FetchRefused, PoliteFetcher, RobotsDisallowed
from horseracing_scrape.models import ParseError, ScrapedOdds
from horseracing_scrape.parse.odds import parse_odds
from horseracing_scrape.urls import win_odds_url
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import guards

POLICY_VERSION = "near_post_policy_v1"
LOGIC_VERSION = (
    "near-post-v1;target=180s;window=60s;collision_step=60s;features=morning_once;"
    "market=market_structure;ens=mev-ens15-v1;single=mev-binary-v2;mix=141"
)
TARGET_S = 180
WINDOW_S = 60
COLLISION_STEP_S = 60
DEADLINE_S = 18.0
SOURCE = "netkeiba"
ODDS_ORIGIN = "https://race.netkeiba.com"
JST = datetime.timezone(datetime.timedelta(hours=9))
DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[3] / "config" / "source-operations.json"
_FINAL_STATUSES = ("captured", "skipped", "rejected", "failed", "dry_run")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# --------------------------------------------------------------------------- policy & robots gates


class NearPostRefused(ValueError):
    """The day cannot start (policy, date, robots) — nothing was fetched or written."""


def load_capture_policy(path: Path | str = DEFAULT_POLICY_PATH) -> dict:
    """The public source-operations policy as data. The capture days are an explicit, approved
    list (scripts/source_operations_172.py validates the full file; this reader only needs the
    keys it gates on). Until 2026-10-31 this also required ``collection_paused`` to be true; the
    user then chose to keep manual refresh and predictions available at all times, so the capture
    now runs under either mode and ops yields the shared request slot for ~35 s around each
    race's fetch instant instead (ops/capture_window.py, 2026-11-02: the worker's fetcher waits
    the hold out before reserving; refreshes are never refused). ops replicates ``schedule_day``
    for that; the shared fixture in specs/174-near-post-decision-quotes/fixtures pins the two."""
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise NearPostRefused(f"policy file missing or a symlink: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw.get("collection_paused"), bool):
        raise NearPostRefused("collection_paused must be a boolean")
    dates = raw.get("capture_only_dates")
    approved_by = raw.get("capture_only_approved_by")
    approved_at = raw.get("capture_only_approved_at")
    if dates is None and approved_by is None and approved_at is None:
        return {"capture_only_dates": frozenset(), "approved": None}
    if not (isinstance(dates, list) and isinstance(approved_by, str) and approved_by.strip()
            and isinstance(approved_at, str)):
        raise NearPostRefused("capture_only_dates / _approved_by / _approved_at must all be present")
    parsed = set()
    for d in dates:
        try:
            day = datetime.date.fromisoformat(str(d))
        except ValueError as exc:
            raise NearPostRefused(f"capture_only_dates has a non-ISO date: {d!r}") from exc
        if day in parsed:
            raise NearPostRefused(f"capture_only_dates repeats {day}")
        parsed.add(day)
    try:
        datetime.datetime.fromisoformat(approved_at)
    except ValueError as exc:
        raise NearPostRefused("capture_only_approved_at is not an ISO datetime") from exc
    return {"capture_only_dates": frozenset(parsed), "approved": {"by": approved_by, "at": approved_at}}


def assert_day_allowed(policy: dict, day: datetime.date, *, today: datetime.date) -> None:
    if day not in policy["capture_only_dates"]:
        raise NearPostRefused(f"{day} is not in capture_only_dates (policy approval required)")
    if day != today:
        raise NearPostRefused(f"{day} is not today ({today} JST): one day, started on that day")


def robots_fresh(now: datetime.datetime, *, cache: robots_cache.RobotsCache | None = None) -> tuple[bool, str]:
    """The shared robots cache must hold a fresh (24 h) entry for the odds origin at launch, so the
    day never starts on an expired policy (171 refreshes it; the fetcher never refreshes it here)."""
    c = cache if cache is not None else robots_cache.shared_cache()
    if c is None:
        return False, "robots cache unavailable"
    entry = c.get(ODDS_ORIGIN)
    if entry is None:
        return False, f"no robots entry for {ODDS_ORIGIN}"
    if not entry.is_fresh(now=now, ttl_s=robots_cache.DEFAULT_TTL_S):
        return False, "robots entry expired (run the 171 refresh within 24 h of launch)"
    return True, "ok"


# --------------------------------------------------------------------------- schedule


@dataclass(frozen=True)
class Scheduled:
    race_id: str
    post_time: datetime.datetime
    target_at: datetime.datetime
    capture_target: str  # "t_minus_3" | "t_minus_4" | ...
    offset_s: int

    @property
    def window_open(self) -> datetime.datetime:
        return self.target_at - datetime.timedelta(seconds=WINDOW_S)

    @property
    def window_close(self) -> datetime.datetime:
        return self.target_at + datetime.timedelta(seconds=WINDOW_S)


def schedule_day(races: Sequence[tuple[str, datetime.datetime]]) -> list[Scheduled]:
    """Targets for a day: ``post_time − 180 s``; races sharing a post time step back 60 s each in
    race_id order so the 60 s shared source interval is kept (2026: 6 such pairs in 91 days)."""
    out: list[Scheduled] = []
    by_post: dict[datetime.datetime, list[str]] = {}
    for rid, pt in races:
        if pt is None or pt.tzinfo is None or pt.utcoffset() is None:
            raise ValueError(f"{rid}: post_time must be timezone-aware")
        by_post.setdefault(pt, []).append(rid)
    for pt in sorted(by_post):
        for k, rid in enumerate(sorted(by_post[pt])):
            offset = TARGET_S + k * COLLISION_STEP_S
            out.append(
                Scheduled(
                    race_id=rid,
                    post_time=pt,
                    target_at=pt - datetime.timedelta(seconds=offset),
                    capture_target=f"t_minus_{offset // 60}",
                    offset_s=offset,
                )
            )
    out.sort(key=lambda s: (s.target_at, s.race_id))
    return out


def window_state(s: Scheduled, now: datetime.datetime) -> str:
    if now < s.window_open:
        return "early"
    if now > s.window_close:
        return "late"
    return "in_window"


# --------------------------------------------------------------------------- morning features


@dataclass(frozen=True)
class DayFeatures:
    """The day's started rows with production as-of features, built once in the morning."""

    day: datetime.date
    frame: pd.DataFrame
    sha256: str
    built_at: datetime.datetime

    def race_rows(self, race_id: str) -> pd.DataFrame:
        rows = self.frame[self.frame["race_id"].astype(str) == str(race_id)]
        return rows.sort_values("horse_number").reset_index(drop=True)


def frame_sha256(frame: pd.DataFrame) -> str:
    canon = frame.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    return hashlib.sha256(canon.to_csv(index=False).encode("utf-8")).hexdigest()


def build_day_features(conn, day: datetime.date, *, clock: Callable[[], datetime.datetime] = _now) -> DayFeatures:
    """``load_rows(through=day)`` → ``build_features`` (the production path), kept for the day."""
    from horseracing_training import market_ev

    raw = market_ev.load_rows(conn, through=day.isoformat())
    feats = market_ev.build_features(raw)
    mine = feats[feats["race_date"].dt.date == day].copy()
    return DayFeatures(day=day, frame=mine, sha256=frame_sha256(mine), built_at=clock())


DEFAULT_FEATURES_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "near_post"


def features_parquet_path(day: datetime.date, sha256: str, features_dir: Path | str = DEFAULT_FEATURES_DIR) -> Path:
    """``<features_dir>/<day>/features-<sha12>.parquet`` — the morning rows, named by their hash."""
    return Path(features_dir) / day.isoformat() / f"features-{sha256[:12]}.parquet"


def save_day_features(day_features: DayFeatures, features_dir: Path | str = DEFAULT_FEATURES_DIR) -> Path | None:
    """175: persist the morning rows so a later scoring run can replay the SAME inputs.

    Append-only (an existing file is never rewritten) and never fatal: a failure here must not
    stop the capture (the rows are a reproduction aid, not an input of the capture). Returns the
    path written, or None when nothing was written (already present, or the write failed)."""
    path = features_parquet_path(day_features.day, day_features.sha256, features_dir)
    if path.exists():
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        day_features.frame.to_parquet(tmp, index=False)
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001 — the capture goes on without the aid
        print(f"WARNING: features parquet not written ({type(exc).__name__}: {exc})", flush=True)
        return None
    return path


def load_day_features(
    day: datetime.date, sha256: str, features_dir: Path | str = DEFAULT_FEATURES_DIR,
) -> DayFeatures | None:
    """The saved morning rows for ``(day, sha256)``, or None when absent or when the re-read frame
    does not hash back to ``sha256`` (a silently different file is worse than none)."""
    path = features_parquet_path(day, sha256, features_dir)
    if not path.is_file():
        return None
    frame = pd.read_parquet(path)
    if frame_sha256(frame) != sha256:
        return None
    built_at = datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.UTC)
    return DayFeatures(day=day, frame=frame, sha256=sha256, built_at=built_at)


# --------------------------------------------------------------------------- prediction


@dataclass(frozen=True)
class PredictionBundle:
    pred: list[dict]
    mix_status: str
    mix_version: str | None
    mix_coefficient_year: int | None
    ensemble_version: str
    ensemble_manifest: str
    ensemble_manifest_sha256: str
    single_version: str
    single_booster: str
    single_booster_sha256: str


class NearPostPredictor(Protocol):
    def predict(self, rows: pd.DataFrame, *, race_date: datetime.date) -> PredictionBundle: ...


def rows_with_quote(rows: pd.DataFrame, quote: ScrapedOdds) -> pd.DataFrame:
    """The race's feature rows with ``odds``/``popularity`` replaced by the quote and the market
    block recomputed by the production ``market_structure``. Every started horse must be quoted
    with a positive price (else ``race_ok`` is False and the caller skips)."""
    from horseracing_training import market_ev

    by_number = {int(r.horse_number): r for r in quote.rows}
    out = rows.copy()
    numbers = out["horse_number"].astype(int).tolist()
    out["odds"] = [
        (float(by_number[n].odds) if n in by_number and by_number[n].odds is not None else np.nan)
        for n in numbers
    ]
    out["popularity"] = [
        (float(by_number[n].popularity) if n in by_number and by_number[n].popularity is not None
         else np.nan)
        for n in numbers
    ]
    out["race_id"] = out["race_id"].astype(str)
    return market_ev.market_structure(out)


class ProductionPredictor:
    """The production artifacts, loaded once per day (boosters for the day's year)."""

    def __init__(self, *, ensemble_dir: Path, single_dir: Path, year: int,
                 ensemble_version: str = "mev-ens15-v1", single_version: str = "mev-binary-v2"):
        from horseracing_training import market_ev

        self.ens = market_ev.EnsembleMarketEvModel.load(ensemble_dir, ensemble_version)
        manifest = self.ens.booster_manifest_for_year(year)
        self.members, self.manifest_sha256 = self.ens.load_members(manifest)
        self.manifest_name = manifest.name
        self.single = market_ev.MarketEvModel.load(single_dir, single_version)
        path = self.single.booster_path_for_year(year)
        self.single_booster, self.single_sha256 = market_ev._read_booster(path)
        self.single_booster_name = path.name

    def predict(self, rows: pd.DataFrame, *, race_date: datetime.date) -> PredictionBundle:
        if not rows["race_ok"].astype(bool).all():
            raise ValueError("race_ok is False: a started horse has no positive quoted price")
        odds = rows["odds"].to_numpy(dtype=float)
        X = self.ens.design_matrix(rows)
        p_ens = np.mean(np.vstack([b.predict(X, num_threads=1) for b in self.members]), axis=0)
        p_single = self.single_booster.predict(self.single.design_matrix(rows), num_threads=1)
        coef = market_mix.coefficients_for(race_date.year)
        p_mix: np.ndarray | None = None
        if not market_mix.MIX_DISPLAY_ENABLED:
            status = "disabled"
        elif coef is None:
            status = "no_coefficients"
        elif self.manifest_name != f"ensemble_{coef.year}.json":
            status = "model_mismatch"  # the same check the race page runs (141)
        else:
            try:
                p_mix = market_mix.mix_probabilities(p_ens.tolist(), odds.tolist(), coef)
                status = "available"
            except ValueError:
                status = "invalid_inputs"
        pred = []
        for i, (hid, hn) in enumerate(zip(rows["horse_id"], rows["horse_number"], strict=True)):
            item = {
                "horse_id": str(hid),
                "horse_number": int(hn),
                "p_ens": float(p_ens[i]),
                "ev_ens": float(p_ens[i] * odds[i]),
                "p_single": float(p_single[i]),
                "ev_single": float(p_single[i] * odds[i]),
                "p_mix": float(p_mix[i]) if p_mix is not None else None,
                "ev_mix": float(p_mix[i] * odds[i]) if p_mix is not None else None,
            }
            pred.append(item)
        return PredictionBundle(
            pred=pred,
            mix_status=status,
            mix_version=market_mix.MIX_VERSION if status == "available" else None,
            mix_coefficient_year=coef.year if (coef is not None and status == "available") else None,
            ensemble_version=self.ens.version,
            ensemble_manifest=self.manifest_name,
            ensemble_manifest_sha256=self.manifest_sha256,
            single_version=self.single.version,
            single_booster=self.single_booster_name,
            single_booster_sha256=self.single_sha256,
        )


# --------------------------------------------------------------------------- capture


@dataclass(frozen=True)
class NearPostReport:
    race_id: str
    status: str  # captured | skipped | rejected | failed | dry_run
    reason: str
    capture_target: str | None = None
    seconds_to_post: int | None = None
    captured_at: datetime.datetime | None = None
    official_at: datetime.datetime | None = None
    decision_quote_id: uuid.UUID | None = None
    n: int | None = None

    @property
    def changed(self) -> bool:
        return self.status == "captured"


def _started(session: Session, race_id: str) -> list[tuple[str, int]]:
    rows = session.execute(
        select(RaceHorse.horse_id, RaceHorse.horse_number)
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
        .order_by(RaceHorse.horse_number)
    ).all()
    return [(str(h), int(n)) for h, n in rows]


def field_digest(started: Sequence[tuple[str, int]]) -> str:
    canon = json.dumps(sorted((h, n) for h, n in started), separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def quote_field(quote: ScrapedOdds, started: Sequence[tuple[str, int]]) -> list[dict]:
    win = {int(r.horse_number): r for r in quote.rows}
    place = {int(r.horse_number): r for r in quote.place_rows}
    out = []
    for hid, hn in sorted(started, key=lambda x: x[1]):
        w = win.get(hn)
        p = place.get(hn)
        out.append(
            {
                "horse_id": hid,
                "horse_number": hn,
                "win_odds": None if w is None or w.odds is None else float(w.odds),
                "popularity": None if w is None or w.popularity is None else int(w.popularity),
                "place_low": None if p is None or p.odds_low is None else float(p.odds_low),
                "place_high": None if p is None or p.odds_high is None else float(p.odds_high),
            }
        )
    return out


def quote_digest(field_rows: Sequence[dict], official_at: datetime.datetime | None) -> str:
    payload = {"official_at": official_at.isoformat() if official_at else None, "field": list(field_rows)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _ensure_deadline(deadline: float, monotonic: Callable[[], float]) -> None:
    if monotonic() >= deadline:
        raise FetchRefused("deadline_exceeded")


def capture_race(
    session: Session,
    *,
    scheduled: Scheduled,
    fetcher: PoliteFetcher,
    predictor: NearPostPredictor,
    day_features: DayFeatures,
    run_id: uuid.UUID,
    dry_run: bool = False,
    clock: Callable[[], datetime.datetime] = _now,
    monotonic: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> NearPostReport:
    """One race, canonical no-fetch-first order; the caller owns the transaction."""
    rid = scheduled.race_id
    target = scheduled.capture_target
    race = session.get(Race, rid)
    if race is None or race.race_date is None:
        return NearPostReport(rid, "rejected", "race_not_found", target)
    pending, _ = guards.is_result_pending(session, rid)
    if not pending:
        return NearPostReport(rid, "skipped", "result_settled", target)
    post_time = race.post_time
    if post_time is None or post_time.tzinfo is None:
        return NearPostReport(rid, "skipped", "post_time_unknown", target)
    if post_time != scheduled.post_time:
        return NearPostReport(rid, "skipped", "post_time_changed", target)
    now = clock()
    if now >= post_time:
        return NearPostReport(rid, "skipped", "post_time_elapsed", target)
    state = window_state(scheduled, now)
    if state != "in_window":
        return NearPostReport(rid, "skipped", f"window_{state}", target)
    complete, _ = guards.entries_complete(session, rid)
    if not complete:
        return NearPostReport(rid, "skipped", "entries_incomplete", target)
    started = _started(session, rid)
    if len(started) < 2:
        return NearPostReport(rid, "skipped", "field_too_small", target)
    if session.scalar(select(DecisionQuote.decision_quote_id).where(DecisionQuote.race_id == rid)):
        return NearPostReport(rid, "skipped", "already_captured", target)
    rows = day_features.race_rows(rid)
    if set(zip(rows["horse_id"].astype(str), rows["horse_number"].astype(int), strict=True)) != set(started):
        return NearPostReport(rid, "skipped", "field_mismatch", target)
    if dry_run:
        return NearPostReport(
            rid, "dry_run", "would_fetch", target,
            seconds_to_post=int((post_time - now).total_seconds()), n=len(started),
        )
    locked = bool(session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": f"near-post:{rid}"}
    ))
    if not locked:
        return NearPostReport(rid, "skipped", "concurrent_capture", target)
    if session.scalar(select(DecisionQuote.decision_quote_id).where(DecisionQuote.race_id == rid)):
        return NearPostReport(rid, "skipped", "already_captured", target)
    deadline = deadline if deadline is not None else monotonic() + DEADLINE_S
    set_deadline = getattr(fetcher, "set_deadline", None)
    if set_deadline is not None:
        set_deadline(deadline)
    try:
        _ensure_deadline(deadline, monotonic)
        payload = fetcher.get(win_odds_url(rid), use_cache=False)
        captured_at = clock()
        quote = parse_odds(payload, rid)
        _ensure_deadline(deadline, monotonic)
    except RobotsDisallowed:
        return NearPostReport(rid, "skipped", "robots_disallowed", target)
    except FetchRefused as exc:
        reason = getattr(exc, "reason", None) or str(exc) or "source_refused"
        return NearPostReport(rid, "skipped", f"source_refused:{reason}", target)
    except (FetchError, ParseError):
        return NearPostReport(rid, "failed", "fetch_failed", target)
    # post-fetch rechecks (the fetch took time)
    pending, _ = guards.is_result_pending(session, rid)
    if not pending:
        return NearPostReport(rid, "skipped", "result_settled_during_fetch", target)
    if _started(session, rid) != started:
        return NearPostReport(rid, "skipped", "field_changed_during_fetch", target)
    if captured_at >= post_time:
        return NearPostReport(rid, "skipped", "post_time_elapsed_during_fetch", target)
    quoted = {int(r.horse_number) for r in quote.rows if r.odds is not None and float(r.odds) >= 1.0}
    if not {n for _, n in started} <= quoted:
        return NearPostReport(rid, "skipped", "quote_incomplete", target)
    try:
        bundle = predictor.predict(rows_with_quote(rows, quote), race_date=race.race_date)
    except ValueError as exc:
        return NearPostReport(rid, "failed", f"predict_failed:{type(exc).__name__}", target)
    seconds_to_post = int((post_time - captured_at).total_seconds())
    frows = quote_field(quote, started)
    row = DecisionQuote(
        race_id=rid,
        captured_at=captured_at,
        official_at=quote.official_at,
        post_time=post_time,
        seconds_to_post=seconds_to_post,
        capture_target=target,
        source=SOURCE,
        n=len(started),
        field=frows,
        pred=bundle.pred,
        quote_digest=quote_digest(frows, quote.official_at),
        field_digest=field_digest(started),
        features_sha256=day_features.sha256,
        ensemble_version=bundle.ensemble_version,
        ensemble_manifest=bundle.ensemble_manifest,
        ensemble_manifest_sha256=bundle.ensemble_manifest_sha256,
        single_version=bundle.single_version,
        single_booster=bundle.single_booster,
        single_booster_sha256=bundle.single_booster_sha256,
        mix_version=bundle.mix_version,
        mix_status=bundle.mix_status,
        mix_coefficient_year=bundle.mix_coefficient_year,
        logic_version=LOGIC_VERSION,
        policy_version=POLICY_VERSION,
        run_id=run_id,
    )
    with session.begin_nested():
        session.add(row)
        session.flush()
    return NearPostReport(
        rid, "captured", "ok", target, seconds_to_post=seconds_to_post, captured_at=captured_at,
        official_at=quote.official_at, decision_quote_id=row.decision_quote_id, n=len(started),
    )


# --------------------------------------------------------------------------- the day


def day_races(session: Session, day: datetime.date) -> list[tuple[str, datetime.datetime]]:
    rows = session.execute(
        select(Race.race_id, Race.post_time)
        .where(Race.race_date == day)
        .where(Race.post_time.is_not(None))
        .where((Race.track_type.is_(None)) | (Race.track_type != "障"))
        .order_by(Race.post_time, Race.race_id)
    ).all()
    return [(str(r), p) for r, p in rows]


@dataclass
class DaySummary:
    day: str
    dry_run: bool
    run_id: str
    features_sha256: str
    features_built_at: str
    n_scheduled: int = 0
    counts: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)
    races: list = field(default_factory=list)
    stopped_early: str | None = None
    in_window_capture_rate: float | None = None
    official_lag_median_s: float | None = None

    def as_dict(self) -> dict:
        return {
            "day": self.day, "dry_run": self.dry_run, "run_id": self.run_id,
            "features_sha256": self.features_sha256, "features_built_at": self.features_built_at,
            "n_scheduled": self.n_scheduled, "counts": self.counts, "reasons": self.reasons,
            "stopped_early": self.stopped_early,
            "in_window_capture_rate": self.in_window_capture_rate,
            "official_lag_median_s": self.official_lag_median_s,
            "logic_version": LOGIC_VERSION, "policy_version": POLICY_VERSION,
            "races": self.races,
        }


def run_day(
    session: Session,
    *,
    day: datetime.date,
    schedule: Sequence[Scheduled],
    fetcher: PoliteFetcher,
    predictor: NearPostPredictor,
    day_features: DayFeatures,
    dry_run: bool = False,
    clock: Callable[[], datetime.datetime] = _now,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    run_id: uuid.UUID | None = None,
) -> DaySummary:
    """Walk the schedule once: wait for each window, capture, commit per race, stop on refusal."""
    run_id = run_id or uuid.uuid4()
    summary = DaySummary(
        day=day.isoformat(), dry_run=dry_run, run_id=str(run_id),
        features_sha256=day_features.sha256, features_built_at=day_features.built_at.isoformat(),
        n_scheduled=len(schedule),
    )
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    lags: list[float] = []
    stopped = False
    for s in schedule:
        if stopped:
            report = NearPostReport(s.race_id, "skipped", "stopped_after_refusal", s.capture_target)
        else:
            wait = (s.window_open - clock()).total_seconds()
            if wait > 0:
                sleep(wait)
            try:
                report = capture_race(
                    session, scheduled=s, fetcher=fetcher, predictor=predictor,
                    day_features=day_features, run_id=run_id, dry_run=dry_run, clock=clock,
                    monotonic=monotonic,
                )
                if report.changed:
                    session.commit()
                else:
                    session.rollback()
            except Exception as exc:  # noqa: BLE001 — one race never aborts the day
                session.rollback()
                report = NearPostReport(s.race_id, "failed", f"error:{type(exc).__name__}", s.capture_target)
            if report.reason.startswith("source_refused"):
                stopped = True
                summary.stopped_early = report.reason
        counts[report.status] += 1
        reasons[report.reason] += 1
        if report.official_at is not None and report.captured_at is not None:
            lags.append((report.captured_at - report.official_at).total_seconds())
        summary.races.append(
            {
                "race_id": report.race_id, "status": report.status, "reason": report.reason,
                "capture_target": report.capture_target, "seconds_to_post": report.seconds_to_post,
                "captured_at": report.captured_at.isoformat() if report.captured_at else None,
                "official_at": report.official_at.isoformat() if report.official_at else None,
                "decision_quote_id": str(report.decision_quote_id) if report.decision_quote_id else None,
                "n": report.n,
            }
        )
    summary.counts = dict(counts)
    summary.reasons = dict(reasons)
    eligible = sum(v for k, v in counts.items() if k in ("captured", "dry_run")) + reasons.get("window_late", 0)
    if eligible:
        summary.in_window_capture_rate = (counts.get("captured", 0) + counts.get("dry_run", 0)) / eligible
    if lags:
        summary.official_lag_median_s = float(np.median(lags))
    return summary

