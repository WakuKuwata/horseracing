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
post time (the same filter as live/near_post.py ``day_races``); the schedule rule lives in
``horseracing_scrape.capture_window`` (Feature 176 moved it there so the JRA official route can
observe the same holds without importing ops; ops must not import live), and is pinned to live's
``schedule_day`` by the shared fixture
``specs/174-near-post-decision-quotes/fixtures/capture-schedule-parity.json``. No process
registration: a hold without a running capture costs a refresh at most ~35 s.

Consumers: the worker's fetcher waits (never refuses) through a hold before each slot reservation
(runner.make_fetcher → politeness ``before_reserve``); the manual predict's 086 snapshot waits the
same way before launching; the refresh routers accept at all times; ``GET /status`` reports
today's holds. Predict, recommend, purchase records and expected_return never touch the source.
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from horseracing_scrape import capture_window as _shared
from horseracing_scrape.capture_window import (  # noqa: F401 — re-exported for ops callers
    CAPTURE_DEADLINE_S,
    COLLISION_STEP_S,
    HOLD_AFTER_S,
    HOLD_BEFORE_MARGIN_S,
    JST,
    JUMP_TRACK,
    TARGET_S,
    WINDOW_S,
    CaptureDay,
    CaptureHold,
    FetchTime,
    day_races,
    fetch_times,
    holds_for,
)
from horseracing_scrape.capture_window import (
    capture_dates as _capture_dates,
)
from horseracing_scrape.politeness import source_interval_s, throttle_key
from sqlalchemy.orm import Session

from .config import _REPO_ROOT, CONFIG

POLICY_PATH_ENV = "OPS_SOURCE_OPERATIONS_PATH"
DEFAULT_POLICY_PATH = _REPO_ROOT / "config" / "source-operations.json"
#: a hold can never be longer than this by construction (interval ≤ 60 s is the policy's range);
#: the waiting hook refuses to wait longer, so a bad clock or policy cannot stall a worker lane.
MAX_WAIT_S = 120.0
_NETKEIBA_URL = "https://race.netkeiba.com/"

log = logging.getLogger(__name__)


def policy_path() -> Path:
    return Path(os.environ.get(POLICY_PATH_ENV) or DEFAULT_POLICY_PATH)


def capture_dates(path: Path | None = None) -> frozenset[datetime.date]:
    """The approved capture days as data. A missing or malformed policy means "no capture days"
    (logged): the hold is a courtesy to the capture, not the source-politeness contract, which the
    shared limiter enforces regardless."""
    return _capture_dates(path or policy_path())


def effective_interval_s() -> float:
    """The interval the shared slot actually enforces for netkeiba from this process: the larger
    of ops's own minimum and the source contract (scrape ``_interval_for``)."""
    return max(float(CONFIG.fetch_min_interval), source_interval_s(throttle_key(_NETKEIBA_URL)))


def capture_day_for(
    session: Session, day: datetime.date, *, interval_s: float | None = None
) -> CaptureDay | None:
    interval = effective_interval_s() if interval_s is None else float(interval_s)
    return _shared.capture_day_for(session, day, interval_s=interval)


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
