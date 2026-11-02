"""変更情報(accessI・当日の全開催)の解析(Feature 176)."""

from __future__ import annotations

import datetime
import re

from ...models import ParseError
from ...venues import build_race_id
from ..models import JraChange
from ..venues import venue_code
from ._common import hhmm_on, nfkc, soup, text_of

_CAPTION_RE = re.compile(r"(\d{1,2})回(\S+?)(\d{1,2})日")
_NUM_NAME_RE = re.compile(r"(\d{1,2})\s*番\s*(\S+)")
_KINDS = {
    "出走取消": "cancel",
    "競走除外": "exclude",
    "発走時刻変更": "post_time",
    "騎手変更": "jockey",
}


def parse_changes(html: str, *, expected_date: datetime.date) -> tuple[JraChange, ...]:
    """Every change on the page, as (race_id, kind, …). An empty page is a valid empty tuple.

    The page is "today's" page; the caller passes the date it believes it is. The page body must
    mention that date (e.g. 「2026年11月1日」) or we refuse — a stale page must not be applied to
    another day."""
    doc = soup(html)
    body = nfkc(doc.get_text(" ", strip=True))
    # The page prints 「11月1日（日曜） 開催情報」 — month/day + weekday, no year. Require both so a
    # page from another day (or the same date in another year, which has a different weekday six
    # years out of seven) is refused.
    weekday = "月火水木金土日"[expected_date.weekday()]
    stamp = f"{expected_date.month}月{expected_date.day}日({weekday}曜)"
    if stamp not in body.replace(" ", ""):
        raise ParseError(f"changes page does not mention {stamp}")
    out: list[JraChange] = []
    for table in doc.select("div.kaisai_list_unit table"):
        cap = text_of(table.select_one("caption"))
        m = _CAPTION_RE.search(cap.replace(" ", ""))
        if not m:
            continue
        kai, venue_name, nichime = int(m.group(1)), m.group(2), int(m.group(3))
        vc = venue_code(venue_name)
        if vc is None:
            raise ParseError(f"changes: unknown venue {venue_name!r}")
        for tr in table.select("tbody tr"):
            th = tr.select_one("th")
            race_no = int(text_of(th)) if th is not None and text_of(th).isdigit() else None
            if race_no is None:
                continue
            race_id = build_race_id(
                year=expected_date.year, track_code=vc, kai=kai, nichime=nichime, race_no=race_no
            )
            if race_id is None:
                raise ParseError(
                    f"changes: cannot build race_id for {vc} {kai}回{nichime}日 {race_no}R"
                )
            for dl in tr.select("dl"):
                kind_txt = text_of(dl.select_one("dt"))
                kind = _KINDS.get(kind_txt)
                if kind is None:  # 馬体重超過・ブリンカー等: not ours (FR-012 scope)
                    continue
                for dd in dl.select("dd"):
                    out.append(_change(race_id, kind, text_of(dd), expected_date))
    return tuple(out)


def _change(race_id: str, kind: str, dd: str, date: datetime.date) -> JraChange:
    if kind in ("cancel", "exclude"):
        m = _NUM_NAME_RE.search(dd)
        if not m:
            raise ParseError(f"changes {race_id}: {kind} without 馬番: {dd!r}")
        return JraChange(race_id, kind, horse_number=int(m.group(1)), horse_name=m.group(2))
    if kind == "post_time":
        times = re.findall(r"\d{1,2}時\d{2}分", dd)
        if len(times) < 2:
            raise ParseError(f"changes {race_id}: post_time change unreadable: {dd!r}")
        return JraChange(
            race_id,
            kind,
            old_post_time=hhmm_on(date, times[0]),
            new_post_time=hhmm_on(date, times[1]),
        )
    # jockey: 「N番 馬名 旧騎手 → 新騎手」 or 「…から…に変更」 (format from the site's own text)
    m = _NUM_NAME_RE.search(dd)
    rest = dd[m.end() :] if m else dd
    old = new = None
    if "→" in rest:
        old, new = (s.strip() or None for s in rest.split("→", 1))
    else:
        mm = re.search(r"(\S+?)\s*から\s*(\S+?)\s*に変更", rest)
        if mm:
            old, new = mm.group(1), mm.group(2)
    if not m or new is None:
        raise ParseError(f"changes {race_id}: jockey change unreadable: {dd!r}")
    return JraChange(
        race_id,
        kind,
        horse_number=int(m.group(1)),
        horse_name=m.group(2),
        old_jockey_name=old,
        new_jockey_name=new,
    )
