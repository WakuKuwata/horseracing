"""Feature 086 pure eligibility and horizon-bucket contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from horseracing_probability.chaos_eligibility import (
    capture_horizon_bucket,
    capture_horizon_buckets,
    confirmation_eligible,
    display_eligible,
    primary_horizon,
    within_primary_horizon,
)


def _artifact(*, minimum: int = 600, maximum: int = 86_400) -> SimpleNamespace:
    return SimpleNamespace(
        preregistration={
            "primary_horizon": {
                "minimum_seconds_to_post": minimum,
                "maximum_seconds_to_post": maximum,
                "basis": "test",
            }
        }
    )


def _field(*horse_ids: str) -> list[dict[str, object]]:
    return [
        {"horse_id": horse_id, "horse_number": number}
        for number, horse_id in enumerate(horse_ids, start=1)
    ]


def _started(*horse_ids: str) -> set[tuple[str, int]]:
    return {(horse_id, number) for number, horse_id in enumerate(horse_ids, start=1)}


def _snapshot(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "status": "active",
        "seconds_to_post": 600,
        "capture_strength": "confirmatory",
        "field": _field("h1", "h2"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_primary_horizon_exposes_only_the_frozen_reporting_contract():
    assert primary_horizon(_artifact()) == {
        "mode": "artifact_seconds_to_post_window",
        "minimum_seconds_to_post": 600,
        "maximum_seconds_to_post": 86_400,
    }


@pytest.mark.parametrize(
    ("seconds_to_post", "expected"),
    ((599, False), (600, True), (86_400, True), (86_401, False)),
)
def test_within_primary_horizon_has_inclusive_bounds(seconds_to_post, expected):
    horizon = _artifact().preregistration["primary_horizon"]

    assert within_primary_horizon(seconds_to_post, horizon) is expected


def test_display_eligibility_requires_active_known_and_unchanged_snapshot():
    started_now = _started("h1", "h2")

    assert display_eligible(_snapshot(), started_now)
    assert not display_eligible(_snapshot(status="void"), started_now)
    assert not display_eligible(_snapshot(seconds_to_post=None), started_now)
    assert not display_eligible(_snapshot(), _started("h1"))


def test_display_and_confirmation_eligibility_are_independent():
    snapshot = _snapshot(capture_strength="weak")
    started_now = _started("h1", "h2")

    assert display_eligible(snapshot, started_now)
    assert not confirmation_eligible(snapshot, _artifact(), started_now)


@pytest.mark.parametrize(
    "snapshot",
    (
        _snapshot(seconds_to_post=None),
        _snapshot(capture_strength="unknown"),
        _snapshot(seconds_to_post=599),
        _snapshot(seconds_to_post=86_401),
    ),
)
def test_confirmation_eligibility_rejects_nonconfirmatory_or_outside_horizon(snapshot):
    assert not confirmation_eligible(snapshot, _artifact(), _started("h1", "h2"))


@pytest.mark.parametrize("seconds_to_post", (600, 86_400))
def test_confirmation_eligibility_includes_both_horizon_boundaries(seconds_to_post):
    snapshot = _snapshot(seconds_to_post=seconds_to_post)

    assert confirmation_eligible(snapshot, _artifact(), _started("h1", "h2"))


def test_default_horizon_bucket_labels_and_bounds_are_exact():
    assert capture_horizon_buckets(600, 86_400) == [
        ("10-30m", 600, 1_799),
        ("30-60m", 1_800, 3_599),
        ("1-3h", 3_600, 10_799),
        ("3-6h", 10_800, 21_599),
        ("6-12h", 21_600, 43_199),
        ("12h+", 43_200, None),
    ]


def test_lower_minimum_changes_the_lowest_label_from_both_ends():
    buckets = capture_horizon_buckets(300, 86_400)

    assert buckets[0] == ("5-30m", 300, 1_799)
    assert capture_horizon_bucket(
        300,
        minimum_seconds_to_post=300,
        maximum_seconds_to_post=86_400,
    ) == "5-30m"
    assert capture_horizon_bucket(
        599,
        minimum_seconds_to_post=300,
        maximum_seconds_to_post=86_400,
    ) == "5-30m"


def test_narrower_maximum_filters_edges_before_enumeration():
    assert capture_horizon_buckets(600, 20_000) == [
        ("10-30m", 600, 1_799),
        ("30-60m", 1_800, 3_599),
        ("1-3h", 3_600, 10_799),
        ("3h+", 10_800, None),
    ]


def test_window_without_an_inner_edge_uses_minutes_for_its_open_bucket():
    assert capture_horizon_buckets(600, 1_700) == [("10m+", 600, None)]
    assert (
        capture_horizon_bucket(
            1_700,
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=1_700,
        )
        == "10m+"
    )


@pytest.mark.parametrize(
    ("seconds_to_post", "expected"),
    (
        (600, "10-30m"),
        (1_799, "10-30m"),
        (1_800, "30-60m"),
        (3_599, "30-60m"),
        (3_600, "1-3h"),
        (43_200, "12h+"),
        (86_400, "12h+"),
    ),
)
def test_bucket_membership_is_inclusive_at_every_relevant_boundary(
    seconds_to_post, expected
):
    assert (
        capture_horizon_bucket(
            seconds_to_post,
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=86_400,
        )
        == expected
    )


def test_bucket_classifier_does_not_round_values_below_the_window():
    with pytest.raises(ValueError, match="outside capture horizon buckets"):
        capture_horizon_bucket(
            599,
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=86_400,
        )


@pytest.mark.parametrize(
    ("maximum", "inside", "expected"),
    ((20_000, 20_000, "3h+"), (86_400, 86_400, "12h+")),
)
def test_open_top_bucket_does_not_accept_values_above_the_artifact_maximum(
    maximum, inside, expected
):
    assert (
        capture_horizon_bucket(
            inside,
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=maximum,
        )
        == expected
    )
    with pytest.raises(ValueError, match="outside capture horizon buckets"):
        capture_horizon_bucket(
            maximum + 1,
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=maximum,
        )
