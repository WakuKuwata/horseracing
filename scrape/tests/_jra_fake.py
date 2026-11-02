"""A network-free JraFetcher stand-in that serves the saved pages by cname prefix (176 tests)."""

from __future__ import annotations

import datetime

from horseracing_scrape.jra.errors import JraParameterError
from horseracing_scrape.jra.models import JraResponse
from tests.jra_fixtures import JST, fetched_at, jra_raw, jra_text

_INDEX_O_HTML = (
    "<html><body><ul>"
    "<li><a href=\"#\" onclick=\"return doAction('/JRADB/accessO.html', 'pw15ols00052026041120261101/00');\">"
    "4回東京11日</a></li>"
    "<li><a href=\"#\" onclick=\"return doAction('/JRADB/accessO.html', 'pw15ols00082026041120261101/00');\">"
    "4回京都11日</a></li>"
    "</ul></body></html>"
)

#: cname prefix → fixture (the O index was not saved during the survey; a synthetic one links the
#: saved Tokyo odds day page — see docs/research survey).
_BY_PREFIX = [
    ("pw01dli00/F3", "index_d_20261101.sjis.html"),
    ("pw01drl0005", "daylist_d_tokyo_20261101.sjis.html"),
    ("pw01des0105", "entries_tokyo_20261101.sjis.html"),
    ("pw01ide01", "changes_20261101.sjis.html"),
    ("pw01sli00/AF", "index_s_20261101.sjis.html"),
    ("pw01srl1005", "daylist_s_tokyo_20261031.sjis.html"),
    ("pw01ses1005", "results_tokyo_20261031.sjis.html"),
    ("pw01hli00/03", "index_h_20261101.sjis.html"),
    ("pw01hde1005", "payouts_tokyo_20261031.sjis.html"),
    ("pw15ols0005", "daylist_o_tokyo_20261101.sjis.html"),
]
_GET_BY_URL = {
    "https://www.jra.go.jp/keiba/baba/_data_cushion.html": "baba_cushion_20261101.sjis.html",
    "https://www.jra.go.jp/keiba/baba/_data_moist.html": "baba_moist_20261101.sjis.html",
}


class FakeJraFetcher:
    """Serves fixtures. ``odds_pages`` maps (race_no → fixture) for the Tokyo odds pages; only 11R
    exists as a real fixture, other race numbers reuse it with the race header rewritten."""

    def __init__(
        self,
        *,
        odds_time_override: str | None = None,
        fail_cnames: set[str] | None = None,
        now: datetime.datetime | None = None,
    ):
        self.calls: list[tuple[str, str | None]] = []
        self.requests_sent = 0
        self._odds_time = odds_time_override
        self._fail = set(fail_cnames or ())
        self._now = now or datetime.datetime(2026, 11, 1, 8, 25, tzinfo=JST)

    def _resp(self, url, cname, name, kind, text=None, raw=None) -> JraResponse:
        raw = raw if raw is not None else jra_raw(name)
        text = text if text is not None else jra_text(name)
        return JraResponse(
            url,
            "x" if cname else None,
            200,
            raw,
            text,
            fetched_at(name) if name else self._now,
            kind,
            "y",
        )

    def get(self, url, *, kind):
        self.calls.append((url, None))
        self.requests_sent += 1
        if url == "https://www.jra.go.jp/robots.txt":
            return JraResponse(
                url, None, 200, b"", "User-agent: *\nDisallow:\n", self._now, kind, "r"
            )
        name = _GET_BY_URL.get(url)
        if name is None:
            raise JraParameterError(f"no fixture for GET {url}", url=url)
        return self._resp(url, None, name, kind)

    def post(self, url, cname, *, kind):
        self.calls.append((url, cname))
        self.requests_sent += 1
        if cname in self._fail:
            raise JraParameterError("injected", url=url)
        if cname == "pw15oli00/6D":
            return JraResponse(
                url, "x", 200, _INDEX_O_HTML.encode(), _INDEX_O_HTML, self._now, kind, "o"
            )
        if cname == "pw01iwtS3/CD":
            return self._resp(url, cname, "accessJ_20261101.json", kind)
        if cname.startswith("pw151ou"):
            return self._odds(url, cname, kind)
        for prefix, name in _BY_PREFIX:
            if cname.startswith(prefix):
                return self._resp(url, cname, name, kind)
        raise JraParameterError(f"no fixture for {cname[:12]}", url=url)

    def _odds(self, url, cname, kind):
        from horseracing_scrape.jra.navigate import odds_cname_race_no

        no = odds_cname_race_no(cname) or 11
        text = jra_text("odds_tokyo11r_20261101_0822.sjis.html")
        if no != 11:
            text = text.replace("race_num_11.png", f"race_num_{no}.png").replace(
                'alt="11レース"', f'alt="{no}レース"'
            )
        if self._odds_time:
            text = text.replace("8時22分現在オッズ", self._odds_time)
        return JraResponse(
            url, "x", 200, text.encode("cp932", "replace"), text, self._now, kind, "z"
        )
