"""馬場情報断片(含水率・クッション値)と accessJ(馬場状態・天候)の解析(Feature 176, US4, FR-020).

* measured readings: the fragment's own 「10月31日（土曜）7時00分」 with the year corrected from
  ``fetched_at`` (a month later than the fetch month → previous year).
* accessJ readings: no measurement time is published, so ``time_basis='fetched'`` and
  ``measured_at = fetched_at``. The cushion time is NOT reused for them (research D10).
"""

from __future__ import annotations

import datetime
import json
import re

from ...models import ParseError
from ..models import JraTrackReading
from ..venues import venue_code
from ._common import JST, nfkc, soup, text_of, to_float

_TIME_RE = re.compile(r"(\d{1,2})月(\d{1,2})日\s*[（(].曜[）)]\s*(\d{1,2})時(\d{2})分")


def measured_at_from(text: str, *, fetched_at: datetime.datetime) -> datetime.datetime:
    m = _TIME_RE.search(nfkc(text))
    if not m:
        raise ParseError(f"measurement time unreadable: {text!r}")
    month, day, hh, mm = (int(x) for x in m.groups())
    fetched_jst = fetched_at.astimezone(JST)
    year = fetched_jst.year
    if month > fetched_jst.month:
        year -= 1  # the fragment lists recent readings only; a later month means last year
    try:
        return datetime.datetime(year, month, day, hh, mm, tzinfo=JST)
    except ValueError as exc:
        raise ParseError(f"measurement time invalid: {text!r}") from exc


def _venue_units(html: str, container_id: str):
    doc = soup(html)
    root = doc.select_one(f"#{container_id}")
    if root is None:
        raise ParseError(f"{container_id} missing")
    for block in root.select("div[id^=rc][title]"):
        vc = venue_code(block.get("title"))
        if vc is None:
            raise ParseError(f"unknown venue title {block.get('title')!r}")
        yield vc, block


def parse_cushion(html: str, *, fetched_at: datetime.datetime) -> tuple[JraTrackReading, ...]:
    out: list[JraTrackReading] = []
    for vc, block in _venue_units(html, "cushion_data_list"):
        for unit in block.select("div.unit"):
            measured = measured_at_from(text_of(unit.select_one(".time")), fetched_at=fetched_at)
            value = to_float(text_of(unit.select_one(".cushion")))
            if value is None:
                raise ParseError(f"cushion value unreadable at {measured}")
            out.append(JraTrackReading(vc, measured, "measured", "cushion_turf", value, None))
    if not out:
        raise ParseError("cushion fragment: no readings")
    return tuple(out)


def parse_moisture(html: str, *, fetched_at: datetime.datetime) -> tuple[JraTrackReading, ...]:
    out: list[JraTrackReading] = []
    for vc, block in _venue_units(html, "moist_data_list"):
        for unit in block.select("div.unit"):
            measured = measured_at_from(text_of(unit.select_one(".time")), fetched_at=fetched_at)
            for surface in ("turf", "dirt"):
                sec = unit.select_one(f".{surface}")
                if sec is None:
                    continue
                for pos, cls in (("goal", "mg"), ("corner4", "m4c")):
                    span = sec.select_one(f"span.{cls}")
                    if span is None:
                        continue
                    value = to_float(text_of(span))
                    if value is None:
                        raise ParseError(f"moisture {surface}/{pos} unreadable at {measured}")
                    out.append(
                        JraTrackReading(
                            vc,
                            measured,
                            "measured",
                            f"moisture_{surface}_{pos}",
                            value,
                            span.get("data-condition"),
                        )
                    )
    if not out:
        raise ParseError("moisture fragment: no readings")
    return tuple(out)


def parse_kaisai_info(
    json_text: str, *, fetched_at: datetime.datetime
) -> tuple[JraTrackReading, ...]:
    """accessJ → going_turf / going_dirt / weather per venue, time_basis='fetched'."""
    try:
        doc = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"accessJ is not JSON: {exc}") from exc
    infos = doc.get("kaisai_info") if isinstance(doc, dict) else None
    if not isinstance(infos, list):
        raise ParseError("accessJ: kaisai_info missing")
    out: list[JraTrackReading] = []
    for info in infos:
        vc = venue_code(info.get("jyoname"))
        if vc is None:
            raise ParseError(f"accessJ: unknown jyoname {info.get('jyoname')!r}")
        for key, item in (("ba_s", "going_turf"), ("ba_d", "going_dirt"), ("weather", "weather")):
            v = nfkc(info.get(key))
            if v:
                out.append(JraTrackReading(vc, fetched_at, "fetched", item, None, v))
    return tuple(out)
