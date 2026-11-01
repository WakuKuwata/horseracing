"""2026-11-02: ops's replica of live's near-post schedule, and the per-race hold arithmetic.

ops must not import live (test_boundary.py), so the T−3 schedule rule is duplicated in
capture_window.py and pinned to live ``schedule_day`` by the shared fixture
``specs/174-near-post-decision-quotes/fixtures/capture-schedule-parity.json`` (written by live;
live/tests/unit/test_near_post_unit.py checks the same file against ``schedule_day``). Regenerate
the fixture only from live, never by hand. No DB, no network.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from horseracing_ops import capture_window as cw

UTC = datetime.UTC
FIXTURE = (Path(__file__).resolve().parents[3] / "specs" / "174-near-post-decision-quotes"
           / "fixtures" / "capture-schedule-parity.json")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _races(fx: dict) -> list[tuple[str, datetime.datetime]]:
    return [(r["race_id"], datetime.datetime.fromisoformat(r["post_time"])) for r in fx["races"]]


def test_constants_match_live():
    c = _fixture()["constants"]
    assert (cw.TARGET_S, cw.WINDOW_S, cw.COLLISION_STEP_S, cw.CAPTURE_DEADLINE_S) == (
        c["target_s"], c["window_s"], c["collision_step_s"], c["deadline_s"])


def test_fetch_times_equal_live_window_open():
    fx = _fixture()
    got = [(rid, fa.isoformat()) for rid, _pt, fa in cw.fetch_times(_races(fx))]
    assert got == [(e["race_id"], e["fetch_at"]) for e in fx["expected"]]


def test_fetch_times_requires_aware_post_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        cw.fetch_times([("202605050303", datetime.datetime(2026, 11, 7, 1, 0))])


def test_hold_brackets_the_fetch_instant():
    fa = datetime.datetime(2026, 11, 7, 0, 46, tzinfo=UTC)
    (h,) = cw.holds_for([("202605050101", fa + datetime.timedelta(seconds=240))], interval_s=10.0)
    assert h.fetch_at == fa
    assert h.starts_at == fa - datetime.timedelta(seconds=10 + cw.HOLD_BEFORE_MARGIN_S)
    assert h.ends_at == fa + datetime.timedelta(seconds=cw.HOLD_AFTER_S)
    assert h.active_at(h.starts_at) and h.active_at(fa) and not h.active_at(h.ends_at)
    # ~35 s at the 10 s interval: the whole point of the redesign
    assert (h.ends_at - h.starts_at).total_seconds() == 35.0


def test_day_envelope_active_and_merged_end():
    fx = _fixture()
    day = cw.CaptureDay(date=datetime.date(2026, 11, 7), holds=cw.holds_for(_races(fx), interval_s=10.0),
                        interval_s=10.0)
    assert day.n_races == 9 and day.hold_seconds == 35.0
    assert day.starts_at == day.holds[0].starts_at and day.ends_at == day.holds[-1].ends_at
    # between holds: nothing open, the next one is known
    between = day.holds[0].ends_at + datetime.timedelta(seconds=1)
    assert day.active_hold(between) is None and day.hold_ends_at(between) is None
    assert day.next_hold(between) is day.holds[1]
    # posts one minute apart (01:15 / 01:16 / 01:17 fetches): holds 35 s long, 60 s apart → no merge
    t = datetime.datetime(2026, 11, 7, 1, 15, 10, tzinfo=UTC)
    assert day.active_hold(t).race_id == "202609050102"
    assert day.hold_ends_at(t) == datetime.datetime(2026, 11, 7, 1, 15, 20, tzinfo=UTC)


def test_overlapping_holds_merge_into_one_pause():
    """Two venues posting 20 s apart (possible, not usual): one pause until the later hold ends."""
    p1 = datetime.datetime(2026, 11, 7, 1, 0, tzinfo=UTC)
    holds = cw.holds_for([("202605050101", p1), ("202608050101", p1 + datetime.timedelta(seconds=20))],
                         interval_s=10.0)
    day = cw.CaptureDay(date=datetime.date(2026, 11, 7), holds=holds, interval_s=10.0)
    t = holds[0].starts_at + datetime.timedelta(seconds=1)
    assert day.active_hold(t) is holds[0]
    assert day.hold_ends_at(t) == holds[1].ends_at
    assert (holds[1].ends_at - holds[0].starts_at).total_seconds() == 55.0 < cw.MAX_WAIT_S


def test_wait_out_hold_sleeps_until_the_hold_ends_and_reevaluates():
    fa = datetime.datetime(2026, 11, 7, 0, 46, tzinfo=UTC)
    (h,) = cw.holds_for([("202605050101", fa + datetime.timedelta(seconds=240))], interval_s=10.0)
    clock = {"now": h.starts_at + datetime.timedelta(seconds=30)}  # 5 s before the hold ends
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        clock["now"] += datetime.timedelta(seconds=s)

    waited = cw.wait_out_hold(lambda: h if h.active_at(clock["now"]) else None,
                              sleep=sleep, now=lambda: clock["now"])
    assert waited == 5.0 and slept == [1.0] * 5
    assert cw.wait_out_hold(lambda: None, sleep=sleep) == 0.0 and len(slept) == 5  # nothing open


def test_wait_out_hold_is_capped():
    far = cw.CaptureHold(
        race_id="x", post_time=datetime.datetime(2030, 1, 1, tzinfo=UTC),
        fetch_at=datetime.datetime(2030, 1, 1, tzinfo=UTC),
        starts_at=datetime.datetime(2000, 1, 1, tzinfo=UTC), ends_at=datetime.datetime(2030, 1, 1, tzinfo=UTC),
    )
    slept: list[float] = []
    waited = cw.wait_out_hold(lambda: far, sleep=slept.append, step_s=10.0,
                              now=lambda: datetime.datetime(2026, 1, 1, tzinfo=UTC))
    assert waited == cw.MAX_WAIT_S and sum(slept) == cw.MAX_WAIT_S


def test_effective_interval_is_the_larger_of_ops_and_source(monkeypatch):
    import dataclasses

    monkeypatch.setenv("HORSERACING_NETKEIBA_MIN_INTERVAL_S", "10")
    monkeypatch.setattr(cw, "CONFIG", dataclasses.replace(cw.CONFIG, fetch_min_interval=1.0))
    assert cw.effective_interval_s() == 10.0
    monkeypatch.setattr(cw, "CONFIG", dataclasses.replace(cw.CONFIG, fetch_min_interval=60.0))
    assert cw.effective_interval_s() == 60.0
