"""Member-rule blackout for back-number downloads (Feature 178, T031; FR-018).

The member rule forbids downloading last year's and older files on race days between 08:00 and
19:00 JST. ``is_archive_blackout`` is a pure function of the clock and the set of race days; the
CLI feeds it the days the DB knows (``races.race_date``) plus the policy's ``capture_only_dates``
(so a day whose races are not yet in the DB still counts). There is no override flag by design.
"""

from __future__ import annotations

import datetime
from collections.abc import Collection

JST = datetime.timezone(datetime.timedelta(hours=9))
BLACKOUT_START = datetime.time(8, 0)
BLACKOUT_END = datetime.time(19, 0)  # exclusive


def is_archive_blackout(now_jst: datetime.datetime, race_days: Collection[datetime.date]) -> bool:
    if now_jst.tzinfo is None:
        raise ValueError("now_jst must be timezone-aware")
    local = now_jst.astimezone(JST)
    if local.date() not in set(race_days):
        return False
    return BLACKOUT_START <= local.time() < BLACKOUT_END


def is_archive_target(file_year: int, now_jst: datetime.datetime) -> bool:
    """The rule speaks of 昨年以前: a yearly pack / a single file of a previous year."""

    return file_year < now_jst.astimezone(JST).year


__all__ = ["BLACKOUT_END", "BLACKOUT_START", "JST", "is_archive_blackout", "is_archive_target"]
