"""Feature 174: pure parts of the near-post capture (no DB, no network)."""

from __future__ import annotations

import datetime
import json

import numpy as np
import pandas as pd
import pytest

from horseracing_live import near_post as np_
from horseracing_scrape.parse.odds import parse_odds

UTC = datetime.UTC
T = datetime.datetime(2026, 10, 31, 6, 0, tzinfo=UTC)


def test_schedule_targets_and_collision_steps():
    races = [("202605050303", T), ("202609050303", T), ("202605050304", T + datetime.timedelta(minutes=10)),
             ("202605050302", T - datetime.timedelta(minutes=30))]
    sched = np_.schedule_day(races)
    # ordered by target time: the t_minus_4 member of the same-post pair comes first
    assert [s.race_id for s in sched] == ["202605050302", "202609050303", "202605050303", "202605050304"]
    by = {s.race_id: s for s in sched}
    assert by["202605050303"].offset_s == 180 and by["202605050303"].capture_target == "t_minus_3"
    assert by["202609050303"].offset_s == 240 and by["202609050303"].capture_target == "t_minus_4"
    assert by["202609050303"].target_at == T - datetime.timedelta(seconds=240)
    assert by["202605050304"].offset_s == 180
    # same-post pair: the two targets are 60 s apart (shared source interval)
    assert (by["202605050303"].target_at - by["202609050303"].target_at).total_seconds() == 60


def test_schedule_requires_aware_post_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        np_.schedule_day([("202605050303", T.replace(tzinfo=None))])


def test_window_state_is_target_plus_minus_60s():
    s = np_.schedule_day([("202605050303", T)])[0]
    assert np_.window_state(s, s.target_at - datetime.timedelta(seconds=61)) == "early"
    assert np_.window_state(s, s.target_at - datetime.timedelta(seconds=60)) == "in_window"
    assert np_.window_state(s, s.target_at + datetime.timedelta(seconds=60)) == "in_window"
    assert np_.window_state(s, s.target_at + datetime.timedelta(seconds=61)) == "late"


def _policy(tmp_path, **extra):
    base = {"schema_version": "172-v1", "source_interval_seconds": 60, "ops_fetch_min_interval_seconds": 60,
            "shared_limiter_enabled": True, "collection_paused": True}
    base.update(extra)
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(base))
    return p


def test_policy_without_capture_days_allows_nothing(tmp_path):
    pol = np_.load_capture_policy(_policy(tmp_path))
    assert pol["capture_only_dates"] == frozenset()
    with pytest.raises(np_.NearPostRefused, match="capture_only_dates"):
        np_.assert_day_allowed(pol, datetime.date(2026, 10, 31), today=datetime.date(2026, 10, 31))


def test_policy_day_gate_requires_listed_and_today(tmp_path):
    pol = np_.load_capture_policy(_policy(
        tmp_path, capture_only_dates=["2026-10-31"], capture_only_approved_by="owner",
        capture_only_approved_at="2026-10-29T10:00:00+09:00"))
    assert pol["approved"] == {"by": "owner", "at": "2026-10-29T10:00:00+09:00"}
    np_.assert_day_allowed(pol, datetime.date(2026, 10, 31), today=datetime.date(2026, 10, 31))
    with pytest.raises(np_.NearPostRefused, match="not today"):
        np_.assert_day_allowed(pol, datetime.date(2026, 10, 31), today=datetime.date(2026, 11, 1))
    with pytest.raises(np_.NearPostRefused, match="capture_only_dates"):
        np_.assert_day_allowed(pol, datetime.date(2026, 11, 1), today=datetime.date(2026, 11, 1))


def test_policy_active_mode_still_allows_capture_days(tmp_path):
    """2026-10-31: collection_paused=false (manual refresh/predict accepted) no longer blocks the
    capture; ops yields the request slot during the window instead."""
    pol = np_.load_capture_policy(_policy(
        tmp_path, collection_paused=False, capture_only_dates=["2026-10-31"],
        capture_only_approved_by="owner", capture_only_approved_at="2026-10-29T10:00:00+09:00"))
    assert pol["capture_only_dates"] == frozenset({datetime.date(2026, 10, 31)})


@pytest.mark.parametrize("extra", [
    {"collection_paused": "true", "capture_only_dates": ["2026-10-31"], "capture_only_approved_by": "o",
     "capture_only_approved_at": "2026-10-29T10:00:00+09:00"},
    {"capture_only_dates": ["2026-10-31"]},
    {"capture_only_dates": ["2026-10-31", "2026-10-31"], "capture_only_approved_by": "o",
     "capture_only_approved_at": "2026-10-29T10:00:00+09:00"},
    {"capture_only_dates": ["31/10/2026"], "capture_only_approved_by": "o",
     "capture_only_approved_at": "2026-10-29T10:00:00+09:00"},
])
def test_policy_rejects_resume_partial_or_malformed(tmp_path, extra):
    with pytest.raises(np_.NearPostRefused):
        np_.load_capture_policy(_policy(tmp_path, **extra))


class _Entry:
    def __init__(self, fresh):
        self._fresh = fresh

    def is_fresh(self, *, now, ttl_s):
        return self._fresh


class _Cache:
    def __init__(self, entry):
        self.entry = entry

    def get(self, origin):
        assert origin == np_.ODDS_ORIGIN
        return self.entry


def test_robots_gate_needs_a_fresh_entry():
    now = T
    assert np_.robots_fresh(now, cache=_Cache(_Entry(True))) == (True, "ok")
    assert np_.robots_fresh(now, cache=_Cache(_Entry(False)))[0] is False
    assert np_.robots_fresh(now, cache=_Cache(None))[0] is False


def _payload(n=4, official="2026-10-31 14:56:40"):
    win = {f"{i:02d}": [str(2.0 * i), "0.0", str(i)] for i in range(1, n + 1)}
    place = {f"{i:02d}": [str(1.1 + 0.1 * i), str(1.5 + 0.2 * i), str(i)] for i in range(1, n + 1)}
    return json.dumps({"data": {"odds": {"1": win, "2": place}, "official_datetime": official}})


def test_quote_field_and_digests_are_deterministic_and_order_free():
    quote = parse_odds(_payload(3), "202605050303")
    started = [("H3", 3), ("H1", 1), ("H2", 2)]
    rows = np_.quote_field(quote, started)
    assert [r["horse_number"] for r in rows] == [1, 2, 3]
    assert rows[1] == {"horse_id": "H2", "horse_number": 2, "win_odds": 4.0, "popularity": 2,
                       "place_low": pytest.approx(1.3), "place_high": pytest.approx(1.9)}
    assert np_.field_digest(started) == np_.field_digest(sorted(started))
    d1 = np_.quote_digest(rows, quote.official_at)
    assert d1 == np_.quote_digest(list(rows), quote.official_at) and len(d1) == 64
    assert quote.official_at.isoformat() == "2026-10-31T14:56:40+09:00"


def test_rows_with_quote_recomputes_the_production_market_block():
    rows = pd.DataFrame({
        "race_id": ["202605050303"] * 3, "horse_id": ["H1", "H2", "H3"], "horse_number": [1.0, 2.0, 3.0],
        "odds": [9.9, 9.9, 9.9], "popularity": [9.0, 9.0, 9.0], "distance": [1600.0] * 3,
    })
    quote = parse_odds(_payload(3), "202605050303")
    out = np_.rows_with_quote(rows, quote)
    assert out["odds"].tolist() == [2.0, 4.0, 6.0]
    assert out["popularity"].tolist() == [1.0, 2.0, 3.0]  # the quote's 人気, not odds_rank
    assert out["race_ok"].all() and abs(out["q"].sum() - 1.0) < 1e-12
    assert out["odds_rank"].tolist() == [1.0, 2.0, 3.0] and out["fav_odds"].tolist() == [2.0] * 3
    assert out["distance"].tolist() == [1600.0] * 3  # untouched


def test_rows_with_quote_marks_unquoted_horse_as_not_race_ok():
    rows = pd.DataFrame({"race_id": ["r"] * 3, "horse_id": list("abc"), "horse_number": [1.0, 2.0, 4.0],
                         "odds": [1.0] * 3, "popularity": [1.0] * 3})
    out = np_.rows_with_quote(rows, parse_odds(_payload(3), "202605050303"))
    assert np.isnan(out["odds"].iloc[2]) and not out["race_ok"].any()


def test_frame_sha256_is_order_independent():
    a = pd.DataFrame({"race_id": ["r1", "r1"], "horse_number": [2.0, 1.0], "x": [1, 2]})
    b = a.iloc[::-1].reset_index(drop=True)
    assert np_.frame_sha256(a) == np_.frame_sha256(b)


# ----------------------------------------------------------------------------- 175: morning parquet


def _day_features(tmp_frame=None):
    frame = tmp_frame if tmp_frame is not None else pd.DataFrame({
        "race_id": ["202610310101"] * 2, "horse_id": ["A", "B"], "horse_number": [1.0, 2.0],
        "odds": [2.5, 4.0], "popularity": [1.0, 2.0], "race_date": pd.to_datetime(["2026-10-31"] * 2),
        "grade": pd.array([None, "G3"], dtype="string"),
    })
    return np_.DayFeatures(day=datetime.date(2026, 10, 31), frame=frame, sha256=np_.frame_sha256(frame),
                           built_at=T)


def test_save_then_load_day_features_roundtrips_sha(tmp_path):
    df = _day_features()
    path = np_.save_day_features(df, tmp_path)
    assert path == tmp_path / "2026-10-31" / f"features-{df.sha256[:12]}.parquet" and path.is_file()
    back = np_.load_day_features(df.day, df.sha256, tmp_path)
    assert back is not None and back.sha256 == df.sha256
    assert np_.frame_sha256(back.frame) == df.sha256
    assert np_.load_day_features(df.day, "0" * 64, tmp_path) is None


def test_save_day_features_never_rewrites_an_existing_file(tmp_path):
    df = _day_features()
    first = np_.save_day_features(df, tmp_path)
    assert first is not None
    before = first.read_bytes()
    assert np_.save_day_features(df, tmp_path) is None
    assert first.read_bytes() == before


def test_save_day_features_failure_is_a_warning_not_an_exception(tmp_path, capsys):
    df = _day_features()
    blocker = tmp_path / "2026-10-31"
    blocker.write_text("not a directory")  # mkdir(parents=True) fails on a file
    assert np_.save_day_features(df, tmp_path) is None
    assert "WARNING: features parquet not written" in capsys.readouterr().out


def test_load_day_features_rejects_a_frame_that_hashes_differently(tmp_path):
    df = _day_features()
    path = np_.save_day_features(df, tmp_path)
    other = df.frame.copy()
    other.loc[0, "odds"] = 9.9
    other.to_parquet(path, index=False)  # tampered content under the old name
    assert np_.load_day_features(df.day, df.sha256, tmp_path) is None


def test_schedule_matches_the_ops_parity_fixture():
    """2026-11-02: ops replicates this schedule (it must not import live) to hold its fetching
    for ~35 s around each race's fetch instant. The shared fixture pins ``window_open`` (the
    instant ``run_day`` sends the one request); regenerate it from THIS function only."""
    from pathlib import Path

    path = (Path(__file__).resolve().parents[3] / "specs" / "174-near-post-decision-quotes"
            / "fixtures" / "capture-schedule-parity.json")
    fx = json.loads(path.read_text(encoding="utf-8"))
    c = fx["constants"]
    assert (np_.TARGET_S, np_.WINDOW_S, np_.COLLISION_STEP_S, np_.DEADLINE_S) == (
        c["target_s"], c["window_s"], c["collision_step_s"], c["deadline_s"])
    races = [(r["race_id"], datetime.datetime.fromisoformat(r["post_time"])) for r in fx["races"]]
    got = [(s.race_id, s.window_open.isoformat(), s.offset_s, s.capture_target)
           for s in np_.schedule_day(races)]
    assert got == [(e["race_id"], e["fetch_at"], e["offset_s"], e["capture_target"])
                   for e in fx["expected"]]
