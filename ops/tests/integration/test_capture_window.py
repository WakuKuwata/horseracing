"""2026-11-02: ops yields the shared request slot to the near-post (T−3) capture per race, briefly.

On a day listed in ``capture_only_dates`` every flat race with a post time has a ~35 s hold around
its capture instant (post − 240 s for the first race of a post time). Inside a hold the worker's
fetcher waits before reserving the slot (never refuses), the manual predict waits before launching
the 086 snapshot, refresh POSTs are accepted as always, and ``GET /status`` reports the holds.
Until 2026-11-02 the hold was the whole race day and refreshes were refused 409 (rejected by the
user: odds move all day).
"""

from __future__ import annotations

import dataclasses
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
        "schema_version": "172-v1", "source_interval_seconds": 10,
        "ops_fetch_min_interval_seconds": 10, "shared_limiter_enabled": True,
        "collection_paused": False, "capture_only_dates": [d.isoformat() for d in dates],
        "capture_only_approved_by": "owner", "capture_only_approved_at": "2026-10-29T10:00:00+09:00",
    }))
    monkeypatch.setenv(cw.POLICY_PATH_ENV, str(path))
    monkeypatch.setenv(st.POLICY_PATH_ENV, str(path))
    monkeypatch.setenv("HORSERACING_NETKEIBA_MIN_INTERVAL_S", "10")
    monkeypatch.setattr(cw, "CONFIG", dataclasses.replace(cw.CONFIG, fetch_min_interval=10.0))
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


def _in_hold_posts(now):
    # first race's capture fetch is 240 s before its post; a post 4 min + 5 s out puts us 5 s
    # into that race's hold (hold = fetch − 15 s … fetch + 20 s); a second race 40 min out
    return [now + datetime.timedelta(seconds=245), now + datetime.timedelta(minutes=40)]


def _between_holds_posts(now):
    # first post 30 min out → its hold opens in ~25.7 min; nothing open now
    return [now + datetime.timedelta(minutes=30), now + datetime.timedelta(minutes=60)]


# ---- hold arithmetic from the DB -------------------------------------------------------------


def test_holds_cover_flat_races_with_a_post_time_only(session):
    day = datetime.date(2030, 1, 4)
    base = datetime.datetime(2030, 1, 4, 0, 50, tzinfo=UTC)
    _seed_day(session, day, [base, base + datetime.timedelta(hours=1),
                             base + datetime.timedelta(hours=7)], jump=(3,))
    d = cw.capture_day_for(session, day, interval_s=10.0)
    assert d is not None and d.n_races == 2  # the jump race is not captured, so has no hold
    first = d.holds[0]
    assert first.fetch_at == base - datetime.timedelta(seconds=240)
    assert first.starts_at == first.fetch_at - datetime.timedelta(seconds=15)
    assert first.ends_at == first.fetch_at + datetime.timedelta(seconds=20)
    assert d.ends_at == base + datetime.timedelta(hours=1) - datetime.timedelta(seconds=220)
    assert cw.capture_day_for(session, datetime.date(2030, 1, 5)) is None


def test_only_listed_days_have_holds(session, tmp_path, monkeypatch):
    now = datetime.datetime.now(UTC)
    _seed_day(session, _today_jst(), _in_hold_posts(now))
    _policy(tmp_path, monkeypatch, [])
    assert cw.today_capture_day(session, now=now) is None
    assert cw.active_hold(session, now=now) is None
    _policy(tmp_path, monkeypatch, [_today_jst()])
    h = cw.active_hold(session, now=now)
    assert h is not None and h.race_id.endswith("01")
    # a few seconds after the first hold ends, nothing is open
    assert cw.active_hold(session, now=h.ends_at + datetime.timedelta(seconds=1)) is None


def test_unreadable_policy_means_no_capture_days(tmp_path, monkeypatch):
    monkeypatch.setenv(cw.POLICY_PATH_ENV, str(tmp_path / "missing.json"))
    assert cw.capture_dates() == frozenset()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert cw.capture_dates(bad) == frozenset()


# ---- routers ----------------------------------------------------------------------------------


def test_refresh_is_accepted_inside_a_hold(client, session, tmp_path, monkeypatch):
    """The 2026-10-31 rule answered 409 for the whole day; now a refresh is always queued."""
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [_today_jst()])
    ids = _seed_day(session, _today_jst(), _in_hold_posts(now))
    assert cw.active_hold(session, now=now) is not None
    r = client.post(f"/ops/v1/races/{ids[0]}/refresh")
    assert r.status_code == 202
    assert session.scalar(select(IngestionJob).where(IngestionJob.scope_value == ids[0])) is not None
    r = client.post(f"/ops/v1/days/{_today_jst().isoformat()}/refresh")
    assert r.status_code == 202


def test_predict_and_recommend_are_not_gated(client, session, tmp_path, monkeypatch):
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [_today_jst()])
    ids = _seed_day(session, _today_jst(), _in_hold_posts(now))
    assert client.post(f"/ops/v1/races/{ids[0]}/predict").status_code == 202
    assert client.post(f"/ops/v1/races/{ids[0]}/recommend").status_code == 202


def test_status_reports_the_holds(client, session, tmp_path, monkeypatch):
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [])
    _seed_day(session, _today_jst(), _in_hold_posts(now))
    assert client.get("/ops/v1/status").json()["capture_window"] is None
    _policy(tmp_path, monkeypatch, [_today_jst()])
    body = client.get("/ops/v1/status").json()
    w = body["capture_window"]
    assert w["active"] is True and w["hold_ends_at"] is not None and w["hold_seconds"] == 35.0
    assert w["date"] == _today_jst().isoformat() and w["n_races"] == 2
    assert "待ってから進みます" in w["detail"] and "受け付けません" not in w["detail"]
    assert body["writes_accepted"] is True

    _seed_day(session, _today_jst(), _between_holds_posts(now))
    w = client.get("/ops/v1/status").json()["capture_window"]
    assert w["active"] is False and w["hold_ends_at"] is None and "拒否はされません" in w["detail"]


# ---- worker / fetcher / predict ---------------------------------------------------------------


def test_io_lane_claims_refresh_inside_a_hold(session, engine, tmp_path, monkeypatch, fixture_fetcher):
    """Claiming is no longer gated: the fetcher waits, the job runs."""
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [_today_jst()])
    ids = _seed_day(session, _today_jst(), _in_hold_posts(now))
    job, _ = enqueue_race(session, ids[0], origin="manual_ui")
    session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    processed = worker_mod._lane_daemon(
        factory, job_types=worker_mod._IO_LANE, interactive_first=False,
        fetcher_factory=lambda: fixture_fetcher, max_iterations=1, idle_sleep=0.0,
    )
    session.refresh(job)
    assert processed == 1 and job.status != "queued"


def test_guarded_fetcher_waits_before_every_reservation(monkeypatch):
    """The hold wait is wired as the politeness ``before_reserve`` hook (runs before EVERY
    reserve attempt — see scrape's test_politeness_contract) and reads the guard each time."""
    fa = datetime.datetime(2026, 11, 7, 0, 46, tzinfo=UTC)
    (hold,) = cw.holds_for([("202605050101", fa + datetime.timedelta(seconds=240))], interval_s=10.0)
    captured: dict = {}

    def fake_background_policy(**kw):
        captured["before_reserve"] = kw["before_reserve"]

        class _Policy:
            def pre_request(self, url):
                pass

            def record_refusal(self, *_a, **_k):
                pass

        return _Policy()

    monkeypatch.setattr(runner_mod, "background_policy", fake_background_policy)
    waits: list = []
    monkeypatch.setattr(cw, "wait_out_hold", lambda guard, **_k: waits.append(guard()) or 0.0)
    answers = [hold, None]
    runner_mod.make_fetcher(capture_guard=lambda: answers.pop(0))
    hook = captured["before_reserve"]
    hook("https://race.netkeiba.com/odds/x")
    hook("https://race.netkeiba.com/odds/x")
    assert waits == [hold, None]  # the guard is re-read on every attempt
    runner_mod.make_fetcher(capture_guard=None)
    assert captured["before_reserve"] is None  # no guard → the policy is unchanged


def test_manual_predict_waits_out_the_hold_then_takes_the_086_snapshot(
    session, tmp_path, monkeypatch
):
    now = datetime.datetime.now(UTC)
    _policy(tmp_path, monkeypatch, [_today_jst()])
    ids = _seed_day(session, _today_jst(), _in_hold_posts(now))  # result-pending by construction
    job, _ = enqueue_predict(session, ids[0], origin="manual_ui")
    session.commit()
    waited: list[float] = []
    monkeypatch.setattr(cw, "wait_out_hold", lambda guard, **_k: waited.append(1) or 7.0)
    launched = []

    def fake_capture(race_id, *, trigger, on_launched):
        launched.append((race_id, trigger))
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"outcome": "skipped", "reason": "x"}), stderr="")

    monkeypatch.setattr(runner_mod, "_live_capture_chaos", fake_capture)
    monkeypatch.setattr(
        runner_mod, "_serving_predict",
        lambda _rid: subprocess.CompletedProcess(args=[], returncode=0, stdout="no races inferred", stderr=""),
    )
    runner_mod.run_predict(session, job)
    assert waited == [1] and launched == [(ids[0], "predict_manual")]
    assert job.summary["capture"]["state"] == "done"
