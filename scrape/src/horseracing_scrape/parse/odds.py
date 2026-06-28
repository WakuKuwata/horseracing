"""単勝オッズ (win odds) parser: real netkeiba odds JSON -> ScrapedOdds (Feature 022).

netkeiba serves win/place odds as JSON (the HTML page renders them via JS), shape:
``{"status": ..., "data": {"odds": {"1": {"01": ["19.1", "0.0", "6"], ...}}}}`` where the inner
key is 馬番 (zero-padded) and the value is ``[単勝オッズ, _, 人気]``. type "1" == 単勝/複勝 group.
The JSON has no race_id, so the caller passes the (URL-validated) race_id. Odds "---.-"/invalid ->
None (excluded at upsert). fail-close: ParseError on missing data.odds["1"].
"""

from __future__ import annotations

import json

from ..models import ParseError, ScrapedOdds, ScrapedOddsRow
from ._common import race_key_from_race_id


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

    return ScrapedOdds(key=race_key_from_race_id(race_id), rows=tuple(rows))
