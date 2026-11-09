"""178 R3 wiring: ``jrdb-persons-collect`` takes the official (5-digit) ids from the DB, walks the
operator-named index page once, fetches only the ids not yet in the jsonl, and reports a stop
that is not ``max_pages`` as exit 1. No network: a fake fetcher serves synthetic pages."""

from __future__ import annotations

import json
from types import SimpleNamespace

from horseracing_scrape.jrdb.cli import run_persons_collect, wanted_person_ids

_INDEX = (
    "<html><body>"
    "<a href=\"#\" onclick=\"return doAction('/JRADB/accessK.html','pw04kmk001019/AB')\">A</a>"
    "<a href=\"#\" onclick=\"return doAction('/JRADB/accessK.html','pw04kmk001234/CD')\">B</a>"
    "</body></html>"
)
_PROFILE = (
    "<html><body><h1>試験　太郎 騎手</h1><dl><dt>所属</dt><dd>美浦</dd>"
    "<dt>生年月日</dt><dd>1990年3月4日</dd></dl></body></html>"
)


class FakeSession:
    def __init__(self, ids):
        self.ids = ids

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, _sql):
        return SimpleNamespace(scalars=lambda: list(self.ids))


class FakeFetcher:
    def __init__(self):
        self.requests_sent = 0
        self.posted = []
        self.got = []

    def post(self, url, cname, *, kind):
        self.requests_sent += 1
        self.posted.append((url, cname, kind))
        text = _INDEX if cname == "idx-cname" else _PROFILE
        return SimpleNamespace(text=text)

    def get(self, url, *, kind):
        self.requests_sent += 1
        self.got.append((url, kind))
        return SimpleNamespace(text=_INDEX)


def _args(out, **kw):
    base = dict(
        kind="jockey",
        index_url=[],
        index_post=[["https://www.jra.go.jp/JRADB/accessK.html", "idx-cname"]],
        out=str(out),
        max_pages=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_wanted_ids_are_the_official_five_digit_ones_only():
    s = FakeSession(["01019", "nk:12345", "01234", None, "1234", "abcde"])
    assert wanted_person_ids(s, "jockey") == ["01019", "01234"]


def test_collect_fetches_only_missing_ids_and_appends(tmp_path):
    out = tmp_path / "jra-profiles.jsonl"
    out.write_text(
        json.dumps(
            {
                "kind": "jockey",
                "canonical_id": "01019",
                "name": "x",
                "birth_date": "1990-01-01",
                "affiliation": None,
                "fetched_at": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    f = FakeFetcher()
    res = run_persons_collect(
        _args(out), fetcher=f, session_factory=lambda: FakeSession(["01019", "01234", "nk:1"])
    )
    job = res.jobs[0]
    assert res.exit_code == 0 and job["wanted"] == 2 and job["already"] == 1
    assert job["fetched"] == 1 and job["not_linked"] == 0 and job["stopped"] is None
    assert res.requests_sent == 2  # the index + one profile
    assert [c for _u, c, _k in f.posted] == ["idx-cname", "pw04kmk001234/CD"]
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["canonical_id"] for r in rows] == ["01019", "01234"]
    assert rows[1]["birth_date"] == "1990-03-04" and rows[1]["name"] == "試験 太郎"
    # a second run fetches nothing (append-only, ids already present are skipped)
    res2 = run_persons_collect(
        _args(out), fetcher=FakeFetcher(), session_factory=lambda: FakeSession(["01019", "01234"])
    )
    assert res2.jobs[0]["fetched"] == 0 and res2.requests_sent == 0


def test_static_get_index_pages_and_post_indexes_are_merged(tmp_path):
    out = tmp_path / "p.jsonl"
    f = FakeFetcher()
    args = _args(
        out,
        index_url=["https://www.jra.go.jp/datafile/meikan/jretirement.html"],
        index_post=[["https://www.jra.go.jp/JRADB/accessK.html", "idx-cname"]],
    )
    res = run_persons_collect(
        args, fetcher=f, session_factory=lambda: FakeSession(["01019", "01234", "09999"])
    )
    job = res.jobs[0]
    assert f.got == [("https://www.jra.go.jp/datafile/meikan/jretirement.html", "person")]
    assert job["linked"] == 2 and job["fetched"] == 2 and job["not_linked"] == 1
    assert res.requests_sent == 4  # GET index + POST index + 2 profiles


def test_no_index_page_is_refused_before_any_request(tmp_path):
    res = run_persons_collect(
        _args(tmp_path / "p.jsonl", index_post=[]),
        fetcher=FakeFetcher(),
        session_factory=lambda: FakeSession(["01019"]),
    )
    assert res.exit_code == 2 and res.requests_sent == 0


def test_a_refusal_stops_the_run_with_exit_1(tmp_path):
    class Refusing(FakeFetcher):
        def post(self, url, cname, *, kind):
            if cname == "idx-cname":
                return super().post(url, cname, kind=kind)
            raise RuntimeError("403")

    out = tmp_path / "p.jsonl"
    res = run_persons_collect(
        _args(out), fetcher=Refusing(), session_factory=lambda: FakeSession(["01019"])
    )
    assert res.exit_code == 1 and res.error == "stopped: RuntimeError"
    assert not out.exists()


def test_all_linked_collects_ids_the_db_does_not_know(tmp_path):
    """R3 (2026-11-09): a newly licensed trainer has only an nk: row in the DB, so the DB-driven
    wanted set misses them; --all-linked adds every id the index pages link to."""

    out = tmp_path / "p.jsonl"
    f = FakeFetcher()
    res = run_persons_collect(
        _args(out, all_linked=True), fetcher=f, session_factory=lambda: FakeSession(["01019"])
    )
    job = res.jobs[0]
    assert job["linked"] == 2 and job["fetched"] == 2 and job["wanted"] == 2
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["canonical_id"] for r in rows) == ["01019", "01234"]
