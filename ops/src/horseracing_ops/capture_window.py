"""The near-post capture holds (2026-11-02): when ops fetching yields to the T−3 record.

Feature 174 records one decision quote per race with ONE request sent at ``fetch_at = post_time −
(180 + k·60) − 60`` s (k = index among races sharing a post time, race_id order; the capture
sleeps until that instant and sends at once — 2026-10-31: 21/21 sent at post − 240.0 s), under an
18 s deadline, and a race has exactly one chance. Every netkeiba request on this machine goes
through the same DB-backed slot (scrape/politeness.py: taken only when due, then pushed one
interval), so an ops request reserved just before ``fetch_at`` makes the capture wait an interval,
and a second one taken while it waits (two IO lanes) pushes it past its deadline.

The first version (2026-10-31) held ALL ops fetching for the whole race day (first post − 6 min
to the last post, ~7 h). The user rejected that: odds move all day and refresh must work on race
days. This version holds only ``[fetch_at − (interval + HOLD_BEFORE_MARGIN_S), fetch_at +
HOLD_AFTER_S]`` per race (≈35 s at the 10 s interval, ≈13 min over a 23-race day):

* before: a slot reserved before the hold starts leaves ``next_allowed_at ≤ fetch_at − margin``,
  so the capture reserves immediately; inside the hold ops does not reserve at all;
* after: covers the capture running up to ~20 s late (process scheduling, its own DB guards)
  with no ops request in between, so it still finishes inside its 18 s deadline.

The holds are DERIVED, not negotiated: a day listed in ``capture_only_dates`` of the public
policy (``config/source-operations.json``) has holds, computed from the DB's flat races with a
post time (the same filter as live/near_post.py ``day_races``); the schedule rule is replicated
here because ops must not import live, and pinned to live's ``schedule_day`` by the shared
fixture ``specs/174-near-post-decision-quotes/fixtures/capture-schedule-parity.json``. No process
registration: a hold without a running capture costs a refresh at most ~35 s.

Consumers: the worker's fetcher waits (never refuses) through a hold before each slot reservation
(runner.make_fetcher → politeness ``before_reserve``); the manual predict's 086 snapshot waits the
same way before launching; the refresh routers accept at all times; ``GET /status`` reports
today's holds. Predict, recommend, purchase records and expected_return never touch the source.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from horseracing_db.models import Race
from horseracing_scrape.politeness import source_interval_s, throttle_key
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import _REPO_ROOT, CONFIG

JST = datetime.timezone(datetime.timedelta(hours=9))
POLICY_PATH_ENV = "OPS_SOURCE_OPERATIONS_PATH"
DEFAULT_POLICY_PATH = _REPO_ROOT / "config" / "source-operations.json"
JUMP_TRACK = "障"

#: live/near_post.py schedule constants, replicated (ops must not import live). Drift between the
#: two is caught by the shared parity fixture (tests/integration/test_capture_window.py).
TARGET_S = 180
WINDOW_S = 60
COLLISION_STEP_S = 60
CAPTURE_DEADLINE_S = 18.0
#: hold = [fetch_at − (interval + HOLD_BEFORE_MARGIN_S), fetch_at + HOLD_AFTER_S]
HOLD_BEFORE_MARGIN_S = 5.0
HOLD_AFTER_S = 20.0
#: a hold can never be longer than this by construction (interval ≤ 60 s is the policy's range);
#: the waiting hook refuses to wait longer, so a bad clock or policy cannot stall a worker lane.
MAX_WAIT_S = 120.0
_NETKEIBA_URL = "https://race.netkeiba.com/"

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CaptureHold:
    """One race's hold: ops reserves no slot inside ``[starts_at, ends_at)``."""

    race_id: str
    post_time: datetime.datetime
    fetch_at: datetime.datetime
    starts_at: datetime.datetime
    ends_at: datetime.datetime

    def active_at(self, now: datetime.datetime) -> bool:
        return self.starts_at <= now < self.ends_at

    def ends_at_jst(self) -> str:
        return self.ends_at.astimezone(JST).strftime("%H:%M:%S")


@dataclasses.dataclass(frozen=True)
class CaptureDay:
    """Today's holds (the policy lists the day and the DB has flat races with a post time)."""

    date: datetime.date
    holds: tuple[CaptureHold, ...]
    interval_s: float

    @property
    def n_races(self) -> int:
        return len(self.holds)

    @property
    def starts_at(self) -> datetime.datetime:
        return self.holds[0].starts_at

    @property
    def ends_at(self) -> datetime.datetime:
        return self.holds[-1].ends_at

    @property
    def hold_seconds(self) -> float:
        return self.interval_s + HOLD_BEFORE_MARGIN_S + HOLD_AFTER_S

    def active_hold(self, now: datetime.datetime) -> CaptureHold | None:
        return next((h for h in self.holds if h.active_at(now)), None)

    def next_hold(self, now: datetime.datetime) -> CaptureHold | None:
        return next((h for h in self.holds if h.starts_at > now), None)

    def hold_ends_at(self, now: datetime.datetime) -> datetime.datetime | None:
        """When fetching resumes: the end of the run of overlapping/adjacent holds that is open
        now (two venues posting a minute apart make one longer pause), else None."""
        h = self.active_hold(now)
        if h is None:
            return None
        end = h.ends_at
        for nxt in self.holds:
            if nxt.starts_at <= end < nxt.ends_at:
                end = nxt.ends_at
        return end


def policy_path() -> Path:
    return Path(os.environ.get(POLICY_PATH_ENV) or DEFAULT_POLICY_PATH)


def capture_dates(path: Path | None = None) -> frozenset[datetime.date]:
    """The approved capture days as data. A missing or malformed policy means "no capture days"
    (logged): the hold is a courtesy to the capture, not the source-politeness contract, which the
    shared limiter enforces regardless."""
    p = path or policy_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        dates = raw.get("capture_only_dates") or []
        return frozenset(datetime.date.fromisoformat(str(d)) for d in dates)
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        log.warning("capture_only_dates unreadable from %s: %s", p, type(exc).__name__)
        return frozenset()


def effective_interval_s() -> float:
    """The interval the shared slot actually enforces for netkeiba from this process: the larger
    of ops's own minimum and the source contract (scrape ``_interval_for``)."""
    return max(float(CONFIG.fetch_min_interval), source_interval_s(throttle_key(_NETKEIBA_URL)))


FetchTime = tuple[str, datetime.datetime, datetime.datetime]


def fetch_times(races: Sequence[tuple[str, datetime.datetime]]) -> list[FetchTime]:
    """``(race_id, post_time, fetch_at)`` per race — live ``schedule_day``'s ``window_open``:
    post − (TARGET_S + k·COLLISION_STEP_S) − WINDOW_S, k = index among same-post races in race_id
    order. Sorted by (fetch_at, race_id)."""
    by_post: dict[datetime.datetime, list[str]] = {}
    for rid, pt in races:
        if pt is None or pt.tzinfo is None or pt.utcoffset() is None:
            raise ValueError(f"{rid}: post_time must be timezone-aware")
        by_post.setdefault(pt, []).append(rid)
    out: list[FetchTime] = []
    for pt in sorted(by_post):
        for k, rid in enumerate(sorted(by_post[pt])):
            offset = TARGET_S + k * COLLISION_STEP_S + WINDOW_S
            out.append((rid, pt, pt - datetime.timedelta(seconds=offset)))
    out.sort(key=lambda t: (t[2], t[0]))
    return out


def holds_for(
    races: Sequence[tuple[str, datetime.datetime]], *, interval_s: float
) -> tuple[CaptureHold, ...]:
    before = datetime.timedelta(seconds=interval_s + HOLD_BEFORE_MARGIN_S)
    after = datetime.timedelta(seconds=HOLD_AFTER_S)
    return tuple(
        CaptureHold(race_id=rid, post_time=pt, fetch_at=fa,
                    starts_at=fa - before, ends_at=fa + after)
        for rid, pt, fa in fetch_times(races)
    )


def day_races(session: Session, day: datetime.date) -> list[tuple[str, datetime.datetime]]:
    """The flat races of ``day`` with a post time — the same selection as live ``day_races``."""
    rows = session.execute(
        select(Race.race_id, Race.post_time)
        .where(Race.race_date == day)
        .where(Race.post_time.is_not(None))
        .where(or_(Race.track_type.is_(None), Race.track_type != JUMP_TRACK))
        .order_by(Race.post_time, Race.race_id)
    ).all()
    return [(str(r), p) for r, p in rows]


def capture_day_for(
    session: Session, day: datetime.date, *, interval_s: float | None = None
) -> CaptureDay | None:
    races = day_races(session, day)
    if not races:
        return None
    interval = effective_interval_s() if interval_s is None else float(interval_s)
    return CaptureDay(date=day, holds=holds_for(races, interval_s=interval), interval_s=interval)


def today_capture_day(
    session: Session, *, now: datetime.datetime, path: Path | None = None,
    interval_s: float | None = None,
) -> CaptureDay | None:
    """Today's (JST) holds when today is an approved capture day, else None."""
    today = now.astimezone(JST).date()
    if today not in capture_dates(path):
        return None
    return capture_day_for(session, today, interval_s=interval_s)


def active_hold(
    session: Session, *, now: datetime.datetime | None = None, path: Path | None = None,
) -> CaptureHold | None:
    """The hold that is open right now, else None."""
    now = now or datetime.datetime.now(datetime.UTC)
    day = today_capture_day(session, now=now, path=path)
    return None if day is None else day.active_hold(now)


def wait_out_hold(
    guard: Callable[[], CaptureHold | None], *, sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC),
    step_s: float = 1.0,
) -> float:
    """Sleep while ``guard()`` reports an open hold, re-evaluating every ``step_s`` (a policy edit
    or a post_time change is honoured mid-wait). Returns the seconds waited. Never waits more than
    ``MAX_WAIT_S`` in total: the holds are ~35 s by construction, so a longer one is a defect and
    must not stall a worker lane."""
    waited = 0.0
    while waited < MAX_WAIT_S:
        hold = guard()
        if hold is None:
            break
        remaining = (hold.ends_at - now()).total_seconds()
        if remaining <= 0:
            break
        step = min(step_s, remaining, MAX_WAIT_S - waited)
        sleep(step)
        waited += step
    else:
        log.warning("capture hold wait capped at %.0fs", MAX_WAIT_S)
    return waited


def hold_detail(day: CaptureDay, *, now: datetime.datetime) -> str:
    h = day.active_hold(now)
    if h is not None:
        return (
            f"発走直前の判断記録(T−3 捕捉)を取得中です(〜{h.ends_at_jst()})。データ更新は"
            f"この約 {day.hold_seconds:.0f} 秒だけ待ってから進みます。"
            "予測・買い目生成・購入記録はそのまま使えます。"
        )
    return (
        f"本日は判断記録(T−3 捕捉)の日です。各レースの発走約 4 分前の"
        f"約 {day.hold_seconds:.0f} 秒だけデータ更新が待たされます(拒否はされません)。"
    )
