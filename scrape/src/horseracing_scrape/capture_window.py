"""The near-post capture holds — pure schedule + DB readers (Feature 174 → shared in 176).

Feature 174 records one decision quote per race with ONE request sent at ``fetch_at = post_time −
(180 + k·60) − 60`` s (k = index among races sharing a post time, race_id order) under an 18 s
deadline, and a race has exactly one chance. Every request on this machine goes through the same
DB-backed slot, so another request reserved just before ``fetch_at`` makes the capture wait an
interval. The hold ``[fetch_at − (interval + HOLD_BEFORE_MARGIN_S), fetch_at + HOLD_AFTER_S]`` per
race is the window inside which other fetchers reserve no slot.

This module is the single implementation of that schedule for every consumer that must not import
``live`` (ops → waits through a hold; the JRA official route, 176 → stops at the next hop,
FR-004). ``ops/capture_window.py`` re-exports it and adds the ops-specific pieces (its own minimum
interval, the wait loop, the status text). The rule is pinned to live's ``schedule_day`` by the
shared fixture ``specs/174-near-post-decision-quotes/fixtures/capture-schedule-parity.json``.

A missing or malformed policy means "no capture days" (fail-open, logged): the hold is a courtesy
to the capture, not the source-politeness contract, which the shared limiter enforces regardless.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from horseracing_db.models import Race
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

JST = datetime.timezone(datetime.timedelta(hours=9))
JUMP_TRACK = "障"

#: live/near_post.py schedule constants, replicated (consumers must not import live).
TARGET_S = 180
WINDOW_S = 60
COLLISION_STEP_S = 60
CAPTURE_DEADLINE_S = 18.0
#: hold = [fetch_at − (interval + HOLD_BEFORE_MARGIN_S), fetch_at + HOLD_AFTER_S]
HOLD_BEFORE_MARGIN_S = 5.0
HOLD_AFTER_S = 20.0

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CaptureHold:
    """One race's hold: no other fetcher reserves a slot inside ``[starts_at, ends_at)``."""

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


def capture_dates(path: Path) -> frozenset[datetime.date]:
    """The approved capture days as data (``capture_only_dates`` of the policy file)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        dates = raw.get("capture_only_dates") or []
        return frozenset(datetime.date.fromisoformat(str(d)) for d in dates)
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        log.warning("capture_only_dates unreadable from %s: %s", path, type(exc).__name__)
        return frozenset()


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
        CaptureHold(
            race_id=rid, post_time=pt, fetch_at=fa, starts_at=fa - before, ends_at=fa + after
        )
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
    session: Session, day: datetime.date, *, interval_s: float
) -> CaptureDay | None:
    races = day_races(session, day)
    if not races:
        return None
    return CaptureDay(
        date=day, holds=holds_for(races, interval_s=float(interval_s)), interval_s=float(interval_s)
    )


def today_capture_day(
    session: Session, *, now: datetime.datetime, path: Path, interval_s: float
) -> CaptureDay | None:
    """Today's (JST) holds when today is an approved capture day, else None."""
    today = now.astimezone(JST).date()
    if today not in capture_dates(path):
        return None
    return capture_day_for(session, today, interval_s=interval_s)


def active_hold(
    session: Session, *, now: datetime.datetime | None = None, path: Path, interval_s: float
) -> CaptureHold | None:
    """The hold that is open right now, else None."""
    now = now or datetime.datetime.now(datetime.UTC)
    day = today_capture_day(session, now=now, path=path, interval_s=interval_s)
    return None if day is None else day.active_hold(now)
