"""The near-post capture window (2026-10-31): when fetching must yield to the T−3 record.

Feature 174 records one decision quote per race, 180 s before post time, with ONE request that has
an 18 s deadline. Every netkeiba request on this machine goes through the same DB-backed slot
(093/172: one request per 60 s), so a refresh job holding that slot makes the capture miss its
window — and a race has exactly one window, never retried. Until 2026-10-31 the two were kept
apart by pausing collection on capture days; the user chose instead to keep manual refresh and
predictions available at all times and have ops yield only while a capture day is in progress.

The window is DERIVED, not negotiated: a day listed in ``capture_only_dates`` of the public
policy (``config/source-operations.json``) is a capture day, and its window runs from
``LEAD_S`` before the first flat race's post time to the last flat race's post time (the capture
schedule lives in live/near_post.py: target post−180 s, ±60 s window, same-time races shifted by
60 s; the lead adds a 60 s margin on top of those). No process registration, so a capture that
was never started still blocks fetching that day — remove the date from the policy to lift it.

Three consumers: the refresh routers (reject 409 so a click gets the reason), the worker's IO
lane (leaves queued refresh jobs in the queue), and the fetcher (a job claimed before the window
opened makes no request inside it). Predict, recommend, purchase records and expected_return
never touch the source and are not gated.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import os
from pathlib import Path

from horseracing_db.models import Race
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import _REPO_ROOT

JST = datetime.timezone(datetime.timedelta(hours=9))
POLICY_PATH_ENV = "OPS_SOURCE_OPERATIONS_PATH"
DEFAULT_POLICY_PATH = _REPO_ROOT / "config" / "source-operations.json"
#: target lead (180) + capture window (60) + one same-time shift (60) + margin (60)
LEAD_S = 180 + 60 + 60 + 60
JUMP_TRACK = "障"

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CaptureWindow:
    date: datetime.date
    starts_at: datetime.datetime
    ends_at: datetime.datetime
    n_races: int

    def active_at(self, now: datetime.datetime) -> bool:
        return self.starts_at <= now < self.ends_at

    def ends_at_jst(self) -> str:
        return self.ends_at.astimezone(JST).strftime("%H:%M")


def policy_path() -> Path:
    return Path(os.environ.get(POLICY_PATH_ENV) or DEFAULT_POLICY_PATH)


def capture_dates(path: Path | None = None) -> frozenset[datetime.date]:
    """The approved capture days as data. A missing or malformed policy means "no capture days"
    (logged): the gate is a courtesy to the capture, not the source-politeness contract, which the
    shared limiter enforces regardless."""
    p = path or policy_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        dates = raw.get("capture_only_dates") or []
        return frozenset(datetime.date.fromisoformat(str(d)) for d in dates)
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        log.warning("capture_only_dates unreadable from %s: %s", p, type(exc).__name__)
        return frozenset()


def window_for_day(session: Session, day: datetime.date) -> CaptureWindow | None:
    """The capture window of ``day`` from the flat races that have a post time, or None."""
    first, last, n = session.execute(
        select(func.min(Race.post_time), func.max(Race.post_time), func.count(Race.race_id))
        .where(Race.race_date == day)
        .where(Race.post_time.is_not(None))
        .where(or_(Race.track_type.is_(None), Race.track_type != JUMP_TRACK))
    ).one()
    if not n or first is None or last is None:
        return None
    return CaptureWindow(
        date=day, starts_at=first - datetime.timedelta(seconds=LEAD_S), ends_at=last, n_races=n
    )


def today_capture_window(
    session: Session, *, now: datetime.datetime, path: Path | None = None
) -> CaptureWindow | None:
    """Today's (JST) capture window when today is an approved capture day, else None."""
    today = now.astimezone(JST).date()
    if today not in capture_dates(path):
        return None
    return window_for_day(session, today)


def active_capture_window(
    session: Session, *, now: datetime.datetime | None = None, path: Path | None = None
) -> CaptureWindow | None:
    """The window when it is open right now, else None."""
    now = now or datetime.datetime.now(datetime.UTC)
    w = today_capture_window(session, now=now, path=path)
    return w if w is not None and w.active_at(now) else None


def refusal_detail(window: CaptureWindow) -> str:
    return (
        f"発走直前の判断記録(T−3 捕捉)を取得中のため、データ更新は {window.ends_at_jst()} まで"
        "受け付けません。予測・買い目生成・購入記録はそのまま使えます。"
    )
