"""2026-10-31: ops yields the shared request slot to the near-post (T−3) capture.

While today's capture window is open — today is listed in ``capture_only_dates`` and the clock is
between (first flat post − 6 min) and the last flat post — a refresh POST is refused 409, the IO
lane leaves queued refresh jobs alone, a fetcher built with the guard refuses before requesting,
and the 086 snapshot on a manual predict is skipped. Predict/recommend/expected_return run as usual.
"""

from __future__ import annotations

import datetime
import json
import subprocess

import pytest
from horseracing_db.models import IngestionJob, Race
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from horseracing_ops import capture_window as cw
from horseracing_ops import runner as runner_mod
from horseracing_ops import worker as worker_mod
from horseracing_ops.enqueue import enqueue_predict, enqueue_race
from horseracing_ops.routers import status as st

pytestmark = pytest.mark.integration

UTC = datetime.UTC


def _policy(tmp_path, monkeypatch, dates):
    path = tmp_path / "source-operations.json"
    path.write_text(json.dumps({
        "schema_version": "172-v1", "source_interval_seconds": 60,
        "ops_fetch_min_interval_seconds": 60, "shared_limiter_enabled": True,
        "collection_paused": False, "capture_only_dates": [d.isoformat() for d in dates],
        "capture_only_approved_by": "owner", "capture_only_approved_at": "2026-10-29T10:00:00+09:00",
    }))
    monkeypatch.setenv(cw.POLICY_PATH_ENV, str(path))
    monkeypatch.setenv(st.POLICY_PATH_ENV, str(path))
    return path


def _seed_day(session, day, posts, *, prefix="2026050401", jump=()):
    """Flat races on ``day`` posting at the given UTC datetimes (plus optional jump races)."""
    ids = []
    for i, post in enumerate(posts, start=1):
        rid = f"{prefix}{i:02d}"
        session.merge(Race(race_id=rid, race_number=i, race_date=day, venue_code="05",
                           post_time=post, track_type="障" if i in jump else "芝"))
        ids.append(rid)
    session.commit()
    return ids


def _today_jst():
    return datetime.datetime.now(cw.JST).date()


def _in_window_posts(now):
    # first post 5 min out → window opened 1 min ago (LEAD_S = 6 min); last post 40 min out
    return [now + datetime.timedelta(minutes=5), now + datetime.timedelta(minutes=40)]


def _future_posts(now):
    # first post 30 min out → window opens in 24 min
    return [now + datetime.timedelta(minutes=30), now + datetime.timedelta(minutes=60)]


# ---- window arithmetic ----------------------------------------------------------------------


def test_window_spans_first_flat_post_minus_lead_to_last_flat_post(session):
    day = datetime.date(2030, 1, 4)
    base = datetime.datetime(2030, 1, 4, 0, 50, tzinfo=UTC)
    _seed_day(session, day, [base, base + datetime.timedelta(hours=1),
                             base + datetime.timedelta(hours=7)], jump=(3,))
    w = cw.window_for_day(session, day)
    assert w is not None and w.n_races == 2  # the jump race is not captured, so not counted
    assert w.starts_at == base - datetime.timedelta(seconds=cw.LEAD_S)
    assert w.ends_at == base + datetime.timedelta(hours=1)  # the jump race's later post is ignored
    assert w.active_at(w.starts_at) and not w.active_at(w.ends_at)
    assert cw.window_for_day(session, datetime.date(2030, 1, 5)) is None


def test_only_listed_days_have_a_window(session, tmp_path, monkeypatch):
    now = datetime.datetime.now(UTC)
    _seed_day(session, _today_jst(), _in_window_posts(now))
    _policy(tmp_path, monkeypatch, [])
    assert cw.today_capture_window(session, now=now) is None
    _policy(tmp_path, monkeypatch, [_today_jst()])
    w = cw.today_capture_window(session, now=now)
    assert w is not None and w.active_at(now)


def test_unreadable_policy_means_no_capture_days(tmp_path, monkeypatch):
    monkeypatch.setenv(cw.POLICY_PATH_ENV, str(tmp_path / "missing.json"))
    assert cw.capture_dates() == frozenset()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert cw.capture_dates(bad) == frozenset()


# ---- routers ----------------------------------------------------------------------------------


def test_refresh_is_refused_409_inside_the_window_and_accepted_outside(
    client, session, tmp_path, monkeypatch
):
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [_today_jst()])
    ids = _seed_day(session, _today_jst(), _in_window_posts(now))
    r = client.post(f"/ops/v1/races/{ids[0]}/refresh")
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "capture_window" and "判断記録" in body["detail"]
    assert session.scalar(select(IngestionJob).where(IngestionJob.scope_value == ids[0])) is None
    r = client.post(f"/ops/v1/days/{_today_jst().isoformat()}/refresh")
    assert r.status_code == 409 and r.json()["code"] == "capture_window"

    # the same day, window not yet open → accepted as before
    _seed_day(session, _today_jst(), _future_posts(now))
    r = client.post(f"/ops/v1/races/{ids[0]}/refresh")
    assert r.status_code == 202


def test_predict_and_recommend_are_not_gated(client, session, tmp_path, monkeypatch):
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [_today_jst()])
    ids = _seed_day(session, _today_jst(), _in_window_posts(now))
    assert client.post(f"/ops/v1/races/{ids[0]}/predict").status_code == 202
    assert client.post(f"/ops/v1/races/{ids[0]}/recommend").status_code == 202


def test_status_reports_the_window(client, session, tmp_path, monkeypatch):
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [])
    _seed_day(session, _today_jst(), _in_window_posts(now))
    assert client.get("/ops/v1/status").json()["capture_window"] is None
    _policy(tmp_path, monkeypatch, [_today_jst()])
    body = client.get("/ops/v1/status").json()
    assert body["capture_window"]["active"] is True
    assert body["capture_window"]["date"] == _today_jst().isoformat()
    assert body["capture_window"]["n_races"] == 2
    assert "受け付けません" in body["capture_window"]["detail"]
    # writes are still accepted in general (only refresh yields)
    assert body["writes_accepted"] is True


# ---- worker / fetcher / predict ---------------------------------------------------------------


def test_io_lane_leaves_refresh_queued_inside_the_window(
    session, engine, tmp_path, monkeypatch, fixture_fetcher
):
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [_today_jst()])
    ids = _seed_day(session, _today_jst(), _in_window_posts(now))
    job, _ = enqueue_race(session, ids[0], origin="manual_ui")
    session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(worker_mod, "POLL_SECONDS", 0.0, raising=False)
    processed = worker_mod._lane_daemon(
        factory, job_types=worker_mod._IO_LANE, interactive_first=False,
        fetcher_factory=lambda: fixture_fetcher, max_iterations=1, idle_sleep=0.0,
    )
    session.refresh(job)
    assert processed == 0 and job.status == "queued"

    # window closed (policy no longer lists today) → the same job is claimed on the next pass
    _policy(tmp_path, monkeypatch, [])
    processed = worker_mod._lane_daemon(
        factory, job_types=worker_mod._IO_LANE, interactive_first=False,
        fetcher_factory=lambda: fixture_fetcher, max_iterations=1, idle_sleep=0.0,
    )
    session.refresh(job)
    assert processed == 1 and job.status != "queued"


def test_io_lane_stops_claiming_when_the_window_opens_mid_pass(
    session, engine, tmp_path, monkeypatch, fixture_fetcher
):
    """A day refresh queues dozens of jobs; the hold is re-evaluated before every claim."""
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [])
    ids = _seed_day(session, _today_jst(), _in_window_posts(now))
    for rid in ids:
        enqueue_race(session, rid, origin="manual_ui")
    session.commit()
    opened = []

    def run_and_open(sess, job, *, fetcher=None):
        # the first job runs; while it runs the policy lists today → the window is now open
        _policy(tmp_path, monkeypatch, [_today_jst()])
        opened.append(job.scope_value)
        job.status = "succeeded"
        sess.commit()

    monkeypatch.setattr(worker_mod, "_run_claimed", run_and_open)
    processed = worker_mod.drain(session, fetcher=fixture_fetcher, job_types=worker_mod._IO_LANE)
    # exactly one job was claimed (ids share a created_at, so the order is by job id); the window
    # opened while it ran and the next claim was skipped, leaving the other job queued
    assert processed == 1 and len(opened) == 1 and opened[0] in ids
    statuses = {j.scope_value: j.status for j in session.scalars(
        select(IngestionJob).where(IngestionJob.job_type == "refresh_race"))}
    other = next(r for r in ids if r != opened[0])
    assert statuses[other] == "queued"


def test_guarded_fetcher_recheck_after_the_slot_wait(monkeypatch):
    window = cw.CaptureWindow(
        date=datetime.date(2026, 10, 31),
        starts_at=datetime.datetime(2026, 10, 31, 0, 44, tzinfo=UTC),
        ends_at=datetime.datetime(2026, 10, 31, 7, 25, tzinfo=UTC), n_races=23,
    )
    answers = [None, window]  # clear before the wait, open after it

    class _Policy:
        def pre_request(self, url):
            pass

        def record_refusal(self, *_a, **_k):
            pass

    monkeypatch.setattr(runner_mod, "background_policy", lambda **_kw: _Policy())
    fetcher = runner_mod.make_fetcher(capture_guard=lambda: answers.pop(0))
    with pytest.raises(runner_mod.CaptureWindowRefused):
        fetcher._before_request("https://race.netkeiba.com/odds/x")


def test_guarded_fetcher_refuses_before_any_request(monkeypatch):
    window = cw.CaptureWindow(
        date=datetime.date(2026, 10, 31),
        starts_at=datetime.datetime(2026, 10, 31, 0, 44, tzinfo=UTC),
        ends_at=datetime.datetime(2026, 10, 31, 7, 25, tzinfo=UTC), n_races=23,
    )
    monkeypatch.setattr(runner_mod, "background_policy", lambda **_kw: None)
    fetcher = runner_mod.make_fetcher(capture_guard=lambda: window)
    with pytest.raises(runner_mod.CaptureWindowRefused, match="capture_window until 16:25"):
        fetcher._before_request("https://race.netkeiba.com/odds/x")
    # a local refusal (408), never a source refusal: record_refusal ignores it → no cooldown
    assert runner_mod.CaptureWindowRefused(window, "u").status_code == 408
    fetcher = runner_mod.make_fetcher(capture_guard=lambda: None)
    fetcher._before_request("https://race.netkeiba.com/odds/x")  # no window → passes through


def test_manual_predict_skips_the_086_snapshot_inside_the_window(
    session, tmp_path, monkeypatch
):
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [_today_jst()])
    ids = _seed_day(session, _today_jst(), _in_window_posts(now))  # result-pending by construction
    job, _ = enqueue_predict(session, ids[0], origin="manual_ui")
    session.commit()
    monkeypatch.setattr(
        runner_mod, "_live_capture_chaos",
        lambda *_a, **_k: pytest.fail("capture subprocess was launched inside the window"),
    )
    calls = []

    def fake_predict(_rid):
        calls.append(1)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="no races inferred", stderr="")

    monkeypatch.setattr(runner_mod, "_serving_predict", fake_predict)
    runner_mod.run_predict(session, job)
    assert calls == [1]  # the prediction itself ran
    assert job.summary["capture"] == {"state": "done", "outcome": "skipped", "reason": "capture_window"}
