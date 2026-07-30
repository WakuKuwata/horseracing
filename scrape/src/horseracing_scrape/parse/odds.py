"""単勝・複勝オッズ parser: real netkeiba odds JSON -> ScrapedOdds (Feature 022 + Phase 0-2).

netkeiba serves win/place odds as JSON (the HTML page renders them via JS), shape::

    {"status": "result",
     "data": {"official_datetime": "2024-12-28 15:50:17",
              "odds": {"1": {"01": ["19.1", "0.0",  "6"], ...},    # 単勝 [odds, _, 人気]
                       "2": {"01": ["2.4",  "3.9",  "5"], ...}}}}  # 複勝 [下限, 上限, 人気]

the inner key being 馬番 (zero-padded). Group "1" is 単勝, group "2" is 複勝 — the SAME response,
so reading 複勝 costs 0 extra requests. The JSON has no race_id, so the caller passes the
(URL-validated) race_id. Odds "---.-"/invalid -> None (excluded at upsert).

fail-close: ParseError on missing data.odds["1"]. 複勝 (group "2") is OPTIONAL — an absent group
yields no place rows rather than an error, so a payload shape change can never take the win odds
down with it (and upsert leaves any existing place quote untouched).

`status` is intentionally NOT persisted: the real fixture returns "result" even when fetched ~1.5
years after the race, so it is a response-status flag, not a settled/pre-race discriminator.
"""

from __future__ import annotations

import datetime
import json

from ..models import ParseError, ScrapedOdds, ScrapedOddsRow, ScrapedPlaceQuoteRow
from ._common import race_key_from_race_id

#: netkeiba's official_datetime is wall-clock JST with no offset.
JST = datetime.timezone(datetime.timedelta(hours=9))


def _to_float(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "", "---.-", "**") else None
    except ValueError:
        return None


def _to_int(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "", "**") else None
    except ValueError:
        return None


def _to_official_at(v: str | None) -> datetime.datetime | None:
    """'2024-12-28 15:50:17' (JST wall clock) -> tz-aware datetime. Unparsable -> None."""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return datetime.datetime.strptime(v.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
    except ValueError:
        return None


def _place_rows(place: object) -> tuple[ScrapedPlaceQuoteRow, ...]:
    """data.odds['2'] -> place quote rows. A half-present or inverted range is dropped to
    (None, None): a one-sided range is not a market quote, and the DB CHECK forbids it anyway."""
    if not isinstance(place, dict):
        return ()
    rows: list[ScrapedPlaceQuoteRow] = []
    for umaban, vals in place.items():
        if not str(umaban).isdigit():
            continue
        lo = _to_float(vals[0]) if isinstance(vals, list) and vals else None
        hi = _to_float(vals[1]) if isinstance(vals, list) and len(vals) > 1 else None
        pop = _to_int(vals[2]) if isinstance(vals, list) and len(vals) > 2 else None
        if lo is None or hi is None or lo <= 0 or hi <= 0 or lo > hi:
            lo = hi = None
        rows.append(ScrapedPlaceQuoteRow(
            horse_number=int(umaban), odds_low=lo, odds_high=hi, popularity=pop
        ))
    return tuple(rows)


def parse_odds(payload: str, race_id: str) -> ScrapedOdds:
    try:
        doc = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as e:
        raise ParseError(f"odds payload is not valid JSON: {e}") from e

    data = doc.get("data") if isinstance(doc, dict) else None
    odds = data.get("odds") if isinstance(data, dict) else None
    win = odds.get("1") if isinstance(odds, dict) else None
    if not isinstance(win, dict) or not win:
        raise ParseError("missing required key data.odds['1'] (win odds) in JSON")

    rows = []
    for umaban, vals in win.items():
        if not str(umaban).isdigit():
            continue
        odds_val = _to_float(vals[0]) if isinstance(vals, list) and vals else None
        pop = _to_int(vals[2]) if isinstance(vals, list) and len(vals) > 2 else None
        rows.append(ScrapedOddsRow(horse_number=int(umaban), odds=odds_val, popularity=pop))

    return ScrapedOdds(
        key=race_key_from_race_id(race_id),
        rows=tuple(rows),
        place_rows=_place_rows(odds.get("2") if isinstance(odds, dict) else None),
        official_at=_to_official_at(data.get("official_datetime")),
    )
