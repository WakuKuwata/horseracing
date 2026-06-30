"""Feature 034: parse the race-level sectional LAP profile from a db.netkeiba.com race page.

The db.netkeiba race page carries a ``ラップタイム`` table:

    <table ... summary="ラップタイム">
      <tr><th>ラップ</th><td class="race_lap_cell">12.6 - 11.1 - ... - 11.9</td></tr>
      <tr><th>ペース</th><td class="race_lap_cell">12.6 - ... - 120.5&nbsp;(36.0-35.5)</td></tr>
    </table>

- ラップ row: per-200m segment times (the leader-based pace profile).
- ペース row: cumulative times; the trailing ``(first-last)`` is the race テン3F / 上がり3F split.

RESULT-derived (post-race) — the upsert/feature layers treat it strictly as-of (constitution II).
Parser is network-free and tested on a saved real fixture.
"""

from __future__ import annotations

import re

from ..models import ParseError, ScrapedLaps
from ._common import race_id_from_html, race_key_from_race_id, soup_of

_PACE_SPLIT = re.compile(r"\(\s*([\d.]+)\s*-\s*([\d.]+)\s*\)")


def _floats(text: str) -> tuple[float, ...]:
    out = []
    for tok in text.replace("\xa0", " ").split("-"):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return tuple(out)


def parse_laps(html: str, *, race_id: str | None = None) -> ScrapedLaps | None:
    """Parse the ラップタイム table. Returns None if the page has no lap section (e.g. a race with
    no recorded sectionals). ``race_id`` may be passed when not derivable from the page."""
    soup = soup_of(html)
    table = soup.select_one('table[summary="ラップタイム"]')
    if table is None:
        return None
    rid = race_id or race_id_from_html(html)
    key = race_key_from_race_id(rid)

    lap_cell = pace_cell = None
    for tr in table.select("tr"):
        th = tr.select_one("th")
        td = tr.select_one("td.race_lap_cell") or tr.select_one("td")
        if th is None or td is None:
            continue
        label = th.get_text(strip=True)
        if label == "ラップ":
            lap_cell = td.get_text(strip=True)
        elif label == "ペース":
            pace_cell = td.get_text(strip=True)

    if not lap_cell:
        return None
    lap_times = _floats(re.sub(_PACE_SPLIT, "", lap_cell))
    if not lap_times:
        raise ParseError(f"ラップタイム table present but no lap values parsed (race {rid})")

    first_3f = last_3f = None
    if pace_cell:
        m = _PACE_SPLIT.search(pace_cell)
        if m:
            first_3f, last_3f = float(m.group(1)), float(m.group(2))

    return ScrapedLaps(key=key, lap_times=lap_times,
                       pace_first_3f=first_3f, pace_last_3f=last_3f)
