"""178 T023: the 段 0 probes — window from the 174 constants, frozen config, dependency
aggregation, HEAD-only arrivals, and no request to netkeiba."""

from __future__ import annotations

import datetime
import json

import pytest

from horseracing_live import near_post
from horseracing_live import source_probe as sp

UTC = datetime.UTC
T = datetime.datetime(2026, 11, 8, 1, 0, tzinfo=UTC)  # 10:00 JST post time


def _windows(*races):
    return sp.windows_for(list(races))


# --- (a) windows and freshness ---------------------------------------------------------------


def test_window_is_derived_from_the_near_post_constants():
    (w,) = _windows(("202605040101", T))
    assert w.offset_s == near_post.TARGET_S
    assert w.window_open == T - datetime.timedelta(seconds=near_post.TARGET_S + near_post.WINDOW_S)
    assert w.window_close == T - datetime.timedelta(seconds=near_post.TARGET_S - near_post.WINDOW_S)
    assert sp.schedule_constants() == {"target_s": 180, "window_s": 60, "collision_step_s": 60}


def test_window_follows_the_constants_when_they_change(monkeypatch):
    monkeypatch.setattr(near_post, "TARGET_S", 300)
    monkeypatch.setattr(near_post, "WINDOW_S", 30)
    (w,) = _windows(("202605040101", T))
    assert w.offset_s == 300
    assert w.window_open == T - datetime.timedelta(seconds=330)
    assert sp.schedule_constants()["target_s"] == 300


def test_collision_step_moves_the_second_race_60s_back():
    a, b = sorted(_windows(("202605040101", T), ("202609040101", T)), key=lambda w: w.race_id)
    assert a.offset_s == 180 and b.offset_s == 240
    assert (a.window_open - b.window_open).total_seconds() == 60


def test_freshness_is_judged_on_official_at_inside_the_window():
    (w,) = _windows(("202605040101", T))
    assert sp.freshness_state(w, w.window_open) == "ok"
    assert sp.freshness_state(w, w.window_close) == "ok"
    assert sp.freshness_state(w, w.window_open - datetime.timedelta(seconds=1)) == "early"
    assert sp.freshness_state(w, w.window_close + datetime.timedelta(seconds=1)) == "late"
    assert sp.freshness_state(w, None) == "missing"


def test_post_time_change_rederives_the_window_with_the_same_offset():
    (w,) = _windows(("202605040101", T))
    later = T + datetime.timedelta(minutes=10)
    w2 = sp.current_window(w, later)
    assert w2.offset_s == 180 and w2.post_time == later
    assert w2.window_open == later - datetime.timedelta(seconds=240)
    assert sp.current_window(w, T) is w


# --- (a) one race through a fake walker -------------------------------------------------------


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeWalker:
    def __init__(self, fetcher, date):
        self.fetcher = fetcher
        self.date = date

    def odds_pages(self, *, venue, want):
        self.fetcher.calls.append((venue, want))
        yield None, "11R", _Resp("page")


class _FakeFetcher:
    def __init__(self):
        self.calls = []


class _Odds:
    def __init__(self, race_id, official_at):
        self.race_id = race_id
        self.official_at = official_at


def test_probe_one_race_records_ok_and_never_touches_the_db_for_writes(tmp_path):
    (w,) = _windows(("202605040111", T))
    fetcher = _FakeFetcher()
    official = w.window_open + datetime.timedelta(seconds=30)
    rec = sp.probe_one_race(
        session=None,
        window=w,
        fetcher=fetcher,
        clock=lambda: w.window_open + datetime.timedelta(seconds=5),
        walker_factory=_FakeWalker,
        parse=lambda text, expected_date: _Odds("202605040111", official),
        post_time_lookup=lambda rid: T,
    )
    assert rec["ok"] is True and rec["reason"] == "ok"
    assert fetcher.calls == [("05", {("05", 11)})]
    assert rec["official_at"] == official.isoformat() and rec["post_time_changed"] is False


def test_probe_one_race_uses_the_changed_post_time():
    (w,) = _windows(("202605040111", T))
    later = T + datetime.timedelta(minutes=5)
    official = T - datetime.timedelta(
        seconds=200
    )  # inside the ORIGINAL window, early for the new one
    rec = sp.probe_one_race(
        session=None,
        window=w,
        fetcher=_FakeFetcher(),
        clock=lambda: T,
        walker_factory=_FakeWalker,
        parse=lambda text, expected_date: _Odds("202605040111", official),
        post_time_lookup=lambda rid: later,
    )
    assert rec["post_time_changed"] is True and rec["reason"] == "early" and rec["ok"] is False


def test_probe_one_race_records_errors_instead_of_raising():
    (w,) = _windows(("202605040111", T))

    class Boom(_FakeWalker):
        def odds_pages(self, *, venue, want):
            raise RuntimeError("down")
            yield  # pragma: no cover

    rec = sp.probe_one_race(
        session=None,
        window=w,
        fetcher=_FakeFetcher(),
        clock=lambda: T,
        walker_factory=Boom,
        parse=None,
        post_time_lookup=lambda rid: T,
    )
    assert rec["ok"] is False and rec["reason"] == "error:RuntimeError"


def test_run_official_odds_waits_for_each_window_and_appends(tmp_path):
    cfg = sp.ProbeConfig(
        day=T.date(),
        modes=("official-odds",),
        schedule_constants=sp.schedule_constants(),
        races=_windows(("202605040101", T), ("202605040102", T + datetime.timedelta(minutes=30))),
        frozen_at=T,
    )
    now = {"t": T - datetime.timedelta(minutes=10)}
    slept = []

    def clock():
        return now["t"]

    def sleep(s):
        slept.append(s)
        now["t"] += datetime.timedelta(seconds=s)

    out = tmp_path / "probe.jsonl"
    recs = sp.run_official_odds(
        None,
        cfg,
        fetcher=_FakeFetcher(),
        out_path=out,
        clock=clock,
        sleep=sleep,
        walker_factory=_FakeWalker,
        parse=lambda text, expected_date: _Odds(expected_date and "x", None),
        post_time_lookup=lambda rid: (
            T if rid.endswith("01") else T + datetime.timedelta(minutes=30)
        ),
    )
    assert [r["race_id"] for r in recs] == ["202605040101", "202605040102"]
    assert slept and all(s > 0 for s in slept)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == 2 and all(line["mode"] == "official-odds" for line in lines)
    summary = sp.summarize_official_odds(recs)
    assert summary["races"] == 2 and summary["ok"] == 0 and "race_id_mismatch" in summary["reasons"]


def test_config_freeze_refuses_an_existing_path(tmp_path):
    cfg = sp.ProbeConfig(
        T.date(), ("dependencies",), sp.schedule_constants(), _windows(("202605040101", T)), T
    )
    p = tmp_path / "c.json"
    sp.freeze_config(cfg, p)
    doc = json.loads(p.read_text())
    assert (
        doc["schedule_constants"]["target_s"] == 180
        and doc["races"][0]["race_id"] == "202605040101"
    )
    with pytest.raises(FileExistsError):
        sp.freeze_config(cfg, p)


# --- (b) dependencies -------------------------------------------------------------------------


def test_dependency_summary_counts_the_eight_categories_and_netkeiba_share():
    rows = [
        ("row_generation", "netkeiba", 60),
        ("row_generation", "jra_official", 12),
        ("row_generation", "unknown", 3),
        ("master_generation", "netkeiba", 20),
        ("master_generation", "jra_van", 50),
        ("current_values", "jockey:jra_official", 70),
        ("current_values", "body_weight:netkeiba", 5),
        ("id_mapping", "netkeiba_surrogate", 7),
        ("id_mapping", "canonical", 68),
        ("results", "jra_official", 24),
        ("final_odds", "netkeiba", 24),
        ("payouts", "jra_official_result", 24),
        ("near_post", "netkeiba", 24),
    ]
    s = sp.summarize_dependencies(rows, back_to_back=3)
    c = s["categories"]
    assert set(c) == set(sp.CATEGORIES)
    assert c["row_generation"]["total"] == 75 and c["row_generation"]["netkeiba"] == 60
    assert c["row_generation"]["netkeiba_share"] == pytest.approx(0.8)
    assert c["current_values"]["netkeiba"] == 5 and c["id_mapping"]["netkeiba"] == 7
    assert c["results"]["netkeiba_share"] == 0.0 and c["near_post"]["netkeiba_share"] == 1.0
    assert c["payouts"]["by_source"] == {"jra_official_result": 24}
    assert s["back_to_back_starters"] == 3
    empty = sp.summarize_dependencies([], back_to_back=0)
    assert empty["categories"]["results"]["netkeiba_share"] is None


# --- (c) arrivals -----------------------------------------------------------------------------


def test_arrivals_use_head_only_on_todays_files_and_record_changes(tmp_path):
    day = datetime.date(2026, 11, 8)
    calls = []
    state = {"lm": datetime.datetime(2026, 11, 8, 9, 0, tzinfo=UTC), "len": 100}

    class Info:
        def __init__(self, lm, ln):
            self.last_modified, self.content_length = lm, ln

    def head(url):
        calls.append(url)
        if "SED" in url:
            raise RuntimeError("404")
        return Info(state["lm"], state["len"])

    out = tmp_path / "a.jsonl"
    st = sp.ArrivalState()
    first = sp.probe_arrivals_once(day, head=head, clock=lambda: T, state=st, out_path=out)
    assert len(first) == len(sp.arrival_targets(day)) == 7
    assert all(c.endswith((".zip",)) for c in calls)
    assert all("261108" in c or "20261108" in c for c in calls)  # today's files only
    second = sp.probe_arrivals_once(day, head=head, clock=lambda: T, state=st, out_path=out)
    assert second == []  # nothing changed → nothing appended
    state["len"] = 101
    third = sp.probe_arrivals_once(day, head=head, clock=lambda: T, state=st, out_path=out)
    assert len(third) == 6  # every present file changed size; the absent SED did not change
    recs = [json.loads(line) for line in out.read_text().splitlines()]
    assert all("source_url_sha256" in r and "url" not in r for r in recs)
    assert not any("jrdb.com" in json.dumps(r) for r in recs)


def test_arrivals_refuse_a_previous_years_day(tmp_path):
    with pytest.raises(ValueError):
        sp.probe_arrivals_once(
            datetime.date(2025, 11, 8),
            head=lambda url: None,
            clock=lambda: T,
            state=sp.ArrivalState(),
            out_path=tmp_path / "x.jsonl",
        )


def test_no_netkeiba_host_in_the_probe_module():
    import inspect

    src = inspect.getsource(sp)
    assert "netkeiba.com" not in src
    assert "refresh_race_win_odds" not in src.replace("``refresh_race_win_odds`` is NOT used", "")
