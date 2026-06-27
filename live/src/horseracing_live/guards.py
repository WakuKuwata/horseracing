"""Fail-closed guards for live serving (Feature 019, R2/FR-001/005/009).

Each guard returns (ok, reason). live_serve evaluates them before any write: a live race must be a
valid JRA-VAN id, result-pending (no results = not yet run — robust signal, no wall-clock), and have
complete entries. odds_present gates only the odds-dependent recommendation step (prediction itself
does not need odds). Nothing here reads results into features (II) — result presence is only the
"already run" signal.
"""

from __future__ import annotations

import re

from horseracing_db.enums import EntryStatus
from horseracing_db.models import RaceHorse, RaceResult
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_RACE_ID = re.compile(r"^[0-9]{12}$")


def valid_race_id(race_id: str) -> tuple[bool, str]:
    if _RACE_ID.match(race_id or ""):
        return True, "ok"
    return False, f"invalid race_id (must be JRA-VAN 12 digits): {race_id!r}"


def is_result_pending(session: Session, race_id: str) -> tuple[bool, str]:
    """True when NO race_results rows exist for the race (= not yet run → live-eligible)."""
    n = session.scalar(
        select(func.count()).select_from(RaceResult).where(RaceResult.race_id == race_id)
    )
    if n and n > 0:
        return False, f"race already has {n} result rows (not result-pending; use retrospective)"
    return True, "ok"


def entries_complete(session: Session, race_id: str) -> tuple[bool, str]:
    """started horses ≥1, every started horse has a horse_number, no duplicate numbers."""
    rows = session.execute(
        select(RaceHorse.horse_number)
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    ).all()
    nums = [n for (n,) in rows]
    if not nums:
        return False, "no started horses (entries missing / scrape incomplete)"
    if any(n is None for n in nums):
        return False, "some started horses have no horse_number (incomplete entries)"
    if len(nums) != len(set(nums)):
        return False, "duplicate horse_number among started horses"
    return True, "ok"


def odds_present(session: Session, race_id: str) -> tuple[bool, str]:
    """Every started horse has a positive pre-race win odds (gates recommendations only)."""
    rows = session.execute(
        select(RaceHorse.odds)
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    ).all()
    if not rows:
        return False, "no started horses"
    missing = sum(1 for (o,) in rows if o is None or float(o) <= 0.0)
    if missing:
        return False, f"{missing} started horse(s) missing pre-race win odds"
    return True, "ok"
