"""開催日レース一覧 (race-list) parser: server-rendered fragment -> ScrapedRaceList.

netkeiba's ``top/race_list_sub.html?kaisai_date=YYYYMMDD`` fragment is plain HTML (no JS) whose
race cards link to ``shutuba.html?race_id={12 digits}`` / ``result.html?race_id=...``. We extract
the distinct 12-digit race_ids in first-seen order — a regex on ``race_id=`` is more robust to
markup redraws than a CSS class. A day with no JRA racing yields an empty list (not an error);
fail-close only when the payload is empty/None (a fetch/markup failure, not a quiet no-race day).

NOTE: the exact fragment markup should be validated against a real capture (``capture-fixture
--kind race_list``) before production use — the race_id link shape is the load-bearing assumption.
"""

from __future__ import annotations

import re

from ..models import ParseError, ScrapedRaceList

_RACE_ID_RE = re.compile(r"race_id=(\d{12})")


def parse_race_list(html: str, kaisai_date: str) -> ScrapedRaceList:
    if not html or not html.strip():
        raise ParseError("empty race-list payload")
    seen: dict[str, None] = {}  # ordered set: first-seen order, deduped
    for rid in _RACE_ID_RE.findall(html):
        seen.setdefault(rid, None)
    return ScrapedRaceList(kaisai_date=kaisai_date, race_ids=tuple(seen))
