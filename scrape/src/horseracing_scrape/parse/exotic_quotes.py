"""PRE-RACE exotic price grid parser (netkeiba odds API ``type=2,4..8``).

This is the pool's own price for EVERY combination, losing ones included — the thing that makes
"which combination is mispriced?" answerable. ``exotic_odds`` cannot answer it: a dividend exists
only for the combination that came in, so selecting on it would be selecting on the outcome.

Payload shape (same envelope as the win-odds API)::

    {"status": "result",
     "data": {"official_datetime": "2026-01-01 15:50:17",
              "odds": {"4": {"0102": ["255.8", "0.0", "42"], ...}}}}

The group key equals the requested ``type``. The combination key is 馬番 zero-padded to 2 digits
and concatenated ("0102" = 1-2, "010203" = 1-2-3), so it splits into fixed-width pairs. Values are
``[odds_low, odds_high, popularity]``; only ワイド uses the high end, and the point-priced types
send "0.0" there — stored as None rather than a fake range.

Numbers can carry thousands separators once they get large ("4,468.8" for a 三連単), which is why
every numeric field goes through ``_to_float``'s comma strip. Missing it silently drops exactly the
long-shot combinations a mispricing search cares about most.
"""

from __future__ import annotations

import datetime
import json

from horseracing_db.selection import canonical_selection

from ..models import ParseError, ScrapedExoticQuotes
from ..urls import EXOTIC_ODDS_TYPES

#: netkeiba's official_datetime is wall-clock JST with no offset.
JST = datetime.timezone(datetime.timedelta(hours=9))

#: horses per combination, by bet type — also the key-width check.
COMBO_SIZE: dict[str, int] = {
    "place": 1, "quinella": 2, "wide": 2, "exacta": 2, "trio": 3, "trifecta": 3,
}


def _to_float(v) -> float | None:
    if not isinstance(v, str):
        return None
    s = v.replace(",", "").strip()
    if s in ("", "---.-", "**", "-"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f > 0 else None


def _to_int(v) -> int | None:
    if not isinstance(v, str):
        return None
    s = v.replace(",", "").strip()
    try:
        return int(s)
    except ValueError:
        return None


def _split_key(key: str, size: int) -> tuple[int, ...] | None:
    """"010203" -> (1, 2, 3). Wrong width or a non-numeric chunk -> None (skipped, not invented)."""
    if len(key) != size * 2 or not key.isdigit():
        return None
    nums = tuple(int(key[i:i + 2]) for i in range(0, len(key), 2))
    return nums if all(n > 0 for n in nums) else None


def parse_exotic_quotes(payload: str, race_id: str, bet_type: str) -> ScrapedExoticQuotes:
    """Parse one bet type's full pre-race grid. fail-close on a missing/empty group."""
    if bet_type not in EXOTIC_ODDS_TYPES:
        raise ValueError(f"no netkeiba odds type for bet_type {bet_type!r}")
    group = str(EXOTIC_ODDS_TYPES[bet_type])
    size = COMBO_SIZE[bet_type]

    try:
        doc = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as e:
        raise ParseError(f"exotic odds payload is not valid JSON: {e}") from e

    data = doc.get("data") if isinstance(doc, dict) else None
    odds = data.get("odds") if isinstance(data, dict) else None
    grid = odds.get(group) if isinstance(odds, dict) else None
    if not isinstance(grid, dict) or not grid:
        raise ParseError(f"missing data.odds[{group!r}] ({bet_type}) in exotic odds JSON")

    quotes: dict[tuple[int, ...], tuple[float, float | None, int | None]] = {}
    for key, vals in grid.items():
        combo = _split_key(str(key), size)
        if combo is None or not isinstance(vals, list) or not vals:
            continue
        low = _to_float(vals[0])
        if low is None:
            continue                       # not yet priced — omit rather than store a zero
        high = _to_float(vals[1]) if len(vals) > 1 else None
        pop = _to_int(vals[2]) if len(vals) > 2 else None
        if bet_type != "wide":
            high = None                    # only ワイド publishes a real range
        elif high is not None and high < low:
            low, high = high, low
        # canonical_selection keeps finishing order for ordered types and sorts the rest, so these
        # keys join to exotic_odds / recommendations without a second convention.
        quotes[tuple(canonical_selection(bet_type, combo))] = (low, high, pop)

    if not quotes:
        raise ParseError(f"no usable {bet_type} combinations in exotic odds JSON")

    raw_dt = data.get("official_datetime") if isinstance(data, dict) else None
    official_at = None
    if isinstance(raw_dt, str) and raw_dt.strip():
        try:
            official_at = datetime.datetime.strptime(
                raw_dt.strip(), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=JST)
        except ValueError:
            official_at = None

    return ScrapedExoticQuotes(
        race_id=race_id, bet_type=bet_type, quotes=quotes, official_at=official_at
    )
