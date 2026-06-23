"""出馬表 (entries) parser: HTML -> ScrapedEntry (fail-close on missing required elements)."""

from __future__ import annotations

from ..models import ParseError, ScrapedEntry, ScrapedEntryHorse, ScrapedRace
from ._common import attr, race_date_from, race_key_from, require, soup_of, to_int


def parse_entries(html: str) -> ScrapedEntry:
    soup = soup_of(html)
    div = require(soup.select_one("div.race"), "div.race")
    key = race_key_from(div)
    race = ScrapedRace(
        key=key,
        race_date=race_date_from(div),
        distance=to_int(attr(div, "data-distance")),
        track_type=attr(div, "data-track-type"),
        going=attr(div, "data-going"),
        weather=attr(div, "data-weather"),
        race_class=attr(div, "data-class"),
    )
    rows = div.select("table.entries tr.horse")
    if not rows:
        raise ParseError("no entry rows")
    horses = tuple(
        ScrapedEntryHorse(
            netkeiba_horse_id=attr(tr, "data-horse-id", required=True),
            horse_name=attr(tr, "data-horse-name"),
            frame=to_int(attr(tr, "data-frame")),
            horse_number=to_int(attr(tr, "data-number")),
            netkeiba_jockey_id=attr(tr, "data-jockey-id"),
            jockey_name=attr(tr, "data-jockey-name"),
            netkeiba_trainer_id=attr(tr, "data-trainer-id"),
            trainer_name=attr(tr, "data-trainer-name"),
            weight=to_int(attr(tr, "data-weight")),
            sex=attr(tr, "data-sex"),
            age=to_int(attr(tr, "data-age")),
            entry_status=attr(tr, "data-status") or "started",
        )
        for tr in rows
    )
    return ScrapedEntry(race=race, horses=horses)
