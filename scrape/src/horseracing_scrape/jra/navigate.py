"""索引 → 開催日 → レースの cname 遷移(Feature 176, contracts/fetch-and-parse.md §2).

cname は ``JraPageRef`` にだけ持ち、run をまたいで再利用しない(FR-001)。固定入口(索引)の cname は
コード定数として持ってよい(取得した動的 cname の永続化が禁止であって、公開された入口ではない)。
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from .errors import JraParameterError
from .models import (
    KIND_DAYLIST,
    KIND_ENTRIES,
    KIND_INDEX_D,
    KIND_INDEX_H,
    KIND_INDEX_O,
    KIND_INDEX_S,
    KIND_ODDS_WIN,
    KIND_PAYOUTS,
    KIND_RESULTS,
    JraPageRef,
    JraResponse,
    WalkReport,
)
from .venues import venue_code

BASE = "https://www.jra.go.jp"
MAX_REWALK = 1

#: Fixed index entry points (public, stable, part of the site's own navigation).
INDEX: dict[str, JraPageRef] = {
    KIND_INDEX_D: JraPageRef(
        f"{BASE}/JRADB/accessD.html", "pw01dli00/F3", label="出馬表", kind=KIND_INDEX_D
    ),
    KIND_INDEX_O: JraPageRef(
        f"{BASE}/JRADB/accessO.html", "pw15oli00/6D", label="オッズ", kind=KIND_INDEX_O
    ),
    KIND_INDEX_S: JraPageRef(
        f"{BASE}/JRADB/accessS.html", "pw01sli00/AF", label="レース結果", kind=KIND_INDEX_S
    ),
    KIND_INDEX_H: JraPageRef(
        f"{BASE}/JRADB/accessH.html", "pw01hli00/03", label="払戻金", kind=KIND_INDEX_H
    ),
}
CHANGES_URL = f"{BASE}/JRADB/accessI.html"
#: 変更情報 entry (fixed, part of the site's navigation like the four indexes)
CHANGES_CNAME = "pw01ide01/4F"
ACCESS_J_URL = f"{BASE}/JRADB/accessJ.html"
ACCESS_J_CNAME = "pw01iwtS3/CD"
BABA_CUSHION_URL = f"{BASE}/keiba/baba/_data_cushion.html"
BABA_MOIST_URL = f"{BASE}/keiba/baba/_data_moist.html"

_LINK_RE = re.compile(
    r"doAction\(\s*'(?P<path>/JRADB/access[A-Z0-9]\.html)'\s*,\s*'(?P<cname>[^']+)'\s*\)"
    r"[^>]*>(?P<label>.*?)</a>",
    re.S,
)
#: 開催日リンクの中身は「4回東京11日」「2026年11月1日(日曜)」等。日付は cname の末尾 8 桁に出る
#: (pw01drl00052026041120261101/3F → 20261101)。見出しと一致するものだけ辿る。
_CNAME_DATE_RE = re.compile(r"(\d{8})(?:Z)?/[0-9A-F]{2}$")
_CNAME_VENUE_KAI_DAY_RE = re.compile(r"(\d{2})(\d{4})(\d{2})(\d{2})(\d{8})(?:Z)?/[0-9A-F]{2}$")
_KAI_VENUE_DAY_RE = re.compile(r"(\d+)回(\S+?)(\d+)日")


@dataclass(frozen=True)
class DayLink:
    ref: JraPageRef
    venue_code: str
    kai: int
    nichime: int
    race_date: datetime.date


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


def links(html: str, *, path_suffix: str | None = None) -> list[tuple[str, str, str]]:
    """All doAction links as (url, cname, label). ``path_suffix`` filters on the access page."""
    out = []
    for m in _LINK_RE.finditer(html):
        path = m.group("path")
        if path_suffix is not None and not path.endswith(path_suffix):
            continue
        out.append((BASE + path, m.group("cname"), _strip_tags(m.group("label"))))
    return out


def _parse_day(cname: str, label: str) -> tuple[str, int, int, datetime.date] | None:
    m = _CNAME_VENUE_KAI_DAY_RE.search(cname)
    if not m:
        return None
    vv, year, kai, day, ymd = m.groups()
    try:
        d = datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
    except ValueError:
        return None
    # The label ("4回東京11日") must agree with the cname: the cname is the server's own
    # encoding but we never walk a link whose text says something else (FR-001 / D15).
    lm = _KAI_VENUE_DAY_RE.search(label.replace(" ", ""))
    if lm:
        if int(lm.group(1)) != int(kai) or int(lm.group(3)) != int(day):
            return None
        vc = venue_code(lm.group(2))
        if vc is not None and vc != vv:
            return None
    return vv, int(kai), int(day), d


def day_links(
    index_html: str, date: datetime.date, *, access: str, kind_prefix: str
) -> list[DayLink]:
    """Links on an index/day-list page that lead to ``date``. Empty → unsupported_date (FR-022).

    ``kind_prefix`` is the cname prefix of the wanted page kind (``pw01drl`` for the D day list,
    ``pw01des`` for the entries page, ``pw01srl``/``pw01ses`` for results, ``pw01hde`` payouts)."""
    out: list[DayLink] = []
    seen: set[str] = set()
    for url, cname, label in links(index_html, path_suffix=access):
        if not cname.startswith(kind_prefix) or cname in seen:
            continue
        parsed = _parse_day(cname, label)
        if parsed is None:
            continue
        vv, kai, day, d = parsed
        if d != date:
            continue
        seen.add(cname)
        out.append(DayLink(JraPageRef(url, cname, label=label, kind=KIND_DAYLIST), vv, kai, day, d))
    return out


def race_links(day_html: str, *, kind_prefix: str = "pw151ou") -> list[JraPageRef]:
    """Per-race links on an odds day page (単複 = pw151ou…; one page per race)."""
    out: list[JraPageRef] = []
    seen: set[str] = set()
    for url, cname, label in links(day_html, path_suffix="accessO.html"):
        if cname.startswith(kind_prefix) and cname not in seen:
            seen.add(cname)
            out.append(JraPageRef(url, cname, label=label, kind=KIND_ODDS_WIN))
    return out


#: (index kind, access page, day-list prefix, page prefix, page kind) per command.
ROUTES: dict[str, tuple[str, str, str, str, str]] = {
    "entries": (KIND_INDEX_D, "accessD.html", "pw01drl", "pw01des", KIND_ENTRIES),
    "results": (KIND_INDEX_S, "accessS.html", "pw01srl", "pw01ses", KIND_RESULTS),
    "payouts": (KIND_INDEX_H, "accessH.html", "pw01hde", "pw01hde", KIND_PAYOUTS),
    "odds": (KIND_INDEX_O, "accessO.html", "pw15", "pw151ou", KIND_ODDS_WIN),
}


class Walker:
    """Walks index → day → page for one date. On a parameter error the walk restarts FROM THE
    INDEX once (fresh cnames — the server-embedded check value of the failed link may be stale);
    the failed POST itself is never re-sent with the same cname. A second failure is final."""

    def __init__(self, fetcher, *, date: datetime.date) -> None:
        self._f = fetcher
        self.date = date
        self.report = WalkReport()
        self._rewalks = 0

    def _note(self, **changes) -> None:
        base = dict(
            rewalks=self._rewalks,
            stopped_for_capture_window=self.report.stopped_for_capture_window,
            unsupported_date=self.report.unsupported_date,
        )
        base.update(changes)
        self.report = WalkReport(**base)

    def _may_rewalk(self) -> bool:
        if self._rewalks >= MAX_REWALK:
            return False
        self._rewalks += 1
        self._note()
        return True

    def index(self, route: str) -> JraResponse:
        kind, *_ = ROUTES[route]
        ref = INDEX[kind]
        return self._f.post(ref.url, ref.cname, kind=kind)

    def _day_pages_once(self, route: str, venue: str | None) -> list[tuple[DayLink, JraResponse]]:
        kind, access, day_prefix, page_prefix, page_kind = ROUTES[route]
        idx = self.index(route)
        days = day_links(idx.text, self.date, access=access, kind_prefix=day_prefix)
        if venue is not None:
            days = [d for d in days if d.venue_code == venue]
        if not days:
            self._note(unsupported_date=1)
            return []
        out = []
        for d in days:
            first = self._f.post(d.ref.url, d.ref.cname, kind=KIND_DAYLIST)
            if page_prefix == day_prefix or route == "odds":
                # payouts: the day link IS the page / odds: the day page carries the race links
                out.append((d, first))
                continue
            inner = day_links(first.text, self.date, access=access, kind_prefix=page_prefix)
            inner = [i for i in inner if i.venue_code == d.venue_code]
            if not inner:
                raise JraParameterError(
                    f"day page for {d.venue_code} {self.date} has no {page_prefix} link",
                    url=d.ref.url,
                )
            out.append((d, self._f.post(inner[0].ref.url, inner[0].ref.cname, kind=page_kind)))
        return out

    def day_pages(
        self, route: str, *, venue: str | None = None
    ) -> list[tuple[DayLink, JraResponse]]:
        """For ``route``, fetch the per-venue DAY page(s) of ``self.date``.

        entries/results: index → drl/srl (day list) → des/ses (the all-races page).
        payouts: index → hde (the all-races page directly).
        odds: index → day list (per-race links)."""
        try:
            return self._day_pages_once(route, venue)
        except JraParameterError:
            if not self._may_rewalk():
                raise
            return self._day_pages_once(route, venue)

    def odds_pages(self, *, venue: str | None = None, want: set[tuple[str, int]] | None = None):
        """index O → day page(s) → every 単複 race page. Yields (DayLink, race_label, response).

        ``want`` = {(venue_code, race_no)} limits the per-race POSTs; the race number is read from
        the cname (``pw151ouS3 05 2026 04 11 01 …``: venue, year, kai, day, RACE), never guessed
        from the link text. A race page that fails re-walks index → day page once and posts the
        FRESH link of the same race."""
        for d, day_page in self.day_pages("odds", venue=venue):
            for ref in race_links(day_page.text):
                no = odds_cname_race_no(ref.cname)
                if want is not None and (no is None or (d.venue_code, no) not in want):
                    continue
                try:
                    resp = self._f.post(ref.url, ref.cname, kind=KIND_ODDS_WIN)
                except JraParameterError:
                    if no is None or not self._may_rewalk():
                        raise
                    fresh = self._day_pages_once("odds", d.venue_code)
                    links_now = [
                        r
                        for _, page in fresh
                        for r in race_links(page.text)
                        if odds_cname_race_no(r.cname) == no
                    ]
                    if not links_now:
                        raise
                    resp = self._f.post(links_now[0].url, links_now[0].cname, kind=KIND_ODDS_WIN)
                yield d, ref.label, resp


_ODDS_CNAME_RE = re.compile(r"^pw15\dou[A-Za-z0-9]{2}(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})(\d{8})Z/")


def odds_cname_race_no(cname: str) -> int | None:
    m = _ODDS_CNAME_RE.match(cname)
    return int(m.group(5)) if m else None
