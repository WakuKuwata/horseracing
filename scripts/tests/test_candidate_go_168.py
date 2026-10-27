"""Metadata-only ranking checks. No DB, fetcher or model inference."""

from __future__ import annotations

import copy
import datetime as dt
import pytest

from scripts.roi_explore.candidate_go_168.assessment import (
    rank_technical_candidates,
    technical_exclusions,
)
from scripts.roi_explore.candidate_go_168.access import inspect_robots

NOW = dt.datetime(2026, 10, 27, 5, tzinfo=dt.UTC)


def example(rid="202605050101", post="2026-10-31T01:00:00+00:00"):
    race = dict(
        race_id=rid,
        race_date="2026-10-31",
        post_time=post,
        venue_code="05",
        track_type="芝",
        result_rows=0,
        started_rows=2,
        entry_rows=2,
    )
    entries = [
        dict(race_id=rid, horse_id=f"h{i}", horse_number=i, entry_status="started")
        for i in (1, 2)
    ]
    return race, entries


def test_empty_saved_data_does_not_produce_a_candidate():
    assert rank_technical_candidates([], [], now=NOW) == ([], [])


def test_order_uses_post_then_race_id_and_ignores_signals():
    a, ea = example("202605050102")
    b, eb = example("202605050101")
    c, ec = example("202605050103", "2026-10-31T02:00:00+00:00")
    a.update(ev=9999, predicted_profit=999999)
    b.update(ev=-9999)
    ledger, candidates = rank_technical_candidates([c, a, b], ea + eb + ec, now=NOW)
    assert [r["race_id"] for r in candidates] == [
        "202605050101",
        "202605050102",
        "202605050103",
    ]
    assert all(
        r["full_card_original_identity_operator_and_go_not_assessed"] for r in ledger
    )


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda r, e: r.update(post_time=None), "post_time_missing_or_invalid"),
        (
            lambda r, e: r.update(post_time="2026-10-31T10:00:00"),
            "post_time_missing_or_invalid",
        ),
        (
            lambda r, e: r.update(
                post_time="2026-10-25T05:00:00+00:00", race_date="2026-10-25"
            ),
            "race_already_started",
        ),
        (
            lambda r, e: r.update(
                post_time="2027-01-01T01:00:00+00:00", race_date="2027-01-01"
            ),
            "unsupported_model_year",
        ),
        (lambda r, e: r.update(result_rows=1), "results_present_or_unknown"),
        (lambda r, e: r.update(track_type="障"), "unsupported_track_type"),
        (lambda r, e: e[1].update(horse_number=1), "field_identity_invalid"),
        (
            lambda r, e: e[1].update(entry_status="unknown"),
            "entries_missing_or_unknown_status",
        ),
        (lambda r, e: r.update(started_rows=3), "entry_count_disagrees"),
        (lambda r, e: r.update(race_date="2026-11-01"), "race_date_post_mismatch"),
    ],
)
def test_incomplete_or_ineligible_metadata_is_excluded(mutate, reason):
    race, entries = example()
    mutate(race, entries)
    assert reason in technical_exclusions(race, entries, now=NOW)


@pytest.mark.parametrize("seconds,passes", [(2700, True), (2699.999999, False)])
def test_selection_cutoff_is_45_minutes_in_utc_even_with_jst_post(seconds, passes):
    post = (NOW + dt.timedelta(seconds=seconds)).astimezone(
        dt.timezone(dt.timedelta(hours=9))
    )
    race, entries = example(post=post.isoformat())
    race["race_date"] = post.date().isoformat()
    reasons = technical_exclusions(race, entries, now=NOW)
    assert ("selection_45_minute_cutoff_elapsed" not in reasons) is passes


def test_exact_datetime_order_does_not_sort_timezone_strings_lexically():
    a, ea = example("202605050101", "2026-10-31T09:50:00+09:00")
    b, eb = example("202605050102", "2026-10-31T01:00:00+00:00")
    original = copy.deepcopy(a)
    _, ordered = rank_technical_candidates([b, a], ea + eb, now=NOW)
    assert ordered[0]["race_id"] == a["race_id"] and a == original


def test_cache_missing_is_not_refreshed_or_created(tmp_path, monkeypatch):
    folder = tmp_path / "not-created"
    monkeypatch.setenv("HORSERACING_ROBOTS_CACHE_DIR", str(folder))
    result = inspect_robots(now=NOW)
    assert result["status"] == "CACHE_MISSING" and result["fresh_now"] is False
    assert not folder.exists()


@pytest.mark.parametrize(
    "fresh,allow,wait,interval,robots,throttle",
    [
        (True, False, 0, 1, False, True),
        (False, True, 0, 1, False, True),
        (True, True, 3, 1, True, True),
        (True, True, 3.000001, 1, True, False),
        (True, True, 0, None, True, False),
    ],
)
def test_current_access_requires_allow_and_bounded_backlog(
    fresh, allow, wait, interval, robots, throttle
):
    from scripts.roi_explore.candidate_go_168.assessment import access_ready_now

    data = dict(
        robots=dict(fresh_now=fresh, parsed_rules_allow_odds_api=allow),
        canonical_throttle_state_now=dict(
            blocked_now=False,
            next_allowed_at=(NOW + dt.timedelta(seconds=wait)).isoformat(),
        ),
        as_of_utc=NOW.isoformat(),
        source_interval_seconds=interval,
    )
    assert access_ready_now(data) == dict(robots=robots, throttle=throttle)
