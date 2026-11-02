"""176 T013: contracts/precedence.md §3 — the per-field transition table, row by row."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, Source

from horseracing_scrape.precedence import (
    GROUP_BODY_WEIGHT,
    GROUP_ENTRY_STATIC,
    GROUP_ENTRY_STATUS,
    GROUP_JOCKEY,
    GROUP_POST_TIME,
    GROUP_RACE_STATIC,
    GROUP_TRAINER,
    JRA_OFFICIAL,
    PAGE_CHANGES,
    PAGE_ENTRIES,
    PAGE_RESULTS,
    TransitionContext,
    field_transition,
)

S, C, X = EntryStatus.STARTED, EntryStatus.CANCELLED, EntryStatus.EXCLUDED
NK = Source.NETKEIBA
T0 = datetime.datetime(2026, 11, 1, 8, 0, tzinfo=datetime.UTC)
T1 = T0 + datetime.timedelta(minutes=10)


def ctx(src=NK, page=PAGE_ENTRIES, stored_src=None, inc_at=None, stored_at=None):
    return TransitionContext(src, inc_at, page, stored_src, stored_at)


# --- entry_status -------------------------------------------------------------


@pytest.mark.parametrize("page", [PAGE_ENTRIES, PAGE_CHANGES, PAGE_RESULTS])
@pytest.mark.parametrize("src", [NK, JRA_OFFICIAL])
def test_started_to_non_starter_is_written_from_any_page(page, src):
    assert field_transition(GROUP_ENTRY_STATUS, S, C, ctx(src, page)).decision == "write"
    assert field_transition(GROUP_ENTRY_STATUS, S, X, ctx(src, page)).decision == "write"


@pytest.mark.parametrize("page", [PAGE_ENTRIES, PAGE_CHANGES, PAGE_RESULTS])
@pytest.mark.parametrize("src", [NK, JRA_OFFICIAL])
@pytest.mark.parametrize("stored", [C, X])
def test_non_starter_to_started_is_rejected_from_every_route(page, src, stored):
    t = field_transition(GROUP_ENTRY_STATUS, stored, S, ctx(src, page))
    assert t.decision == "reject" and t.reason == "started_regression"


@pytest.mark.parametrize("stored, inc", [(C, X), (X, C)])
def test_cancel_exclude_correction_only_from_changes_or_results(stored, inc):
    assert (
        field_transition(GROUP_ENTRY_STATUS, stored, inc, ctx(page=PAGE_CHANGES)).decision
        == "write"
    )
    assert (
        field_transition(GROUP_ENTRY_STATUS, stored, inc, ctx(page=PAGE_RESULTS)).decision
        == "write"
    )
    assert (
        field_transition(GROUP_ENTRY_STATUS, stored, inc, ctx(page=PAGE_ENTRIES)).decision == "keep"
    )


def test_entry_status_missing_or_unchanged_is_kept():
    assert field_transition(GROUP_ENTRY_STATUS, C, None, ctx()).decision == "keep"
    assert field_transition(GROUP_ENTRY_STATUS, S, S, ctx()).decision == "keep"
    assert field_transition(GROUP_ENTRY_STATUS, None, S, ctx()).decision == "write"


# --- jockey / body_weight (observation-time rule) ----------------------------


@pytest.mark.parametrize("group, value", [(GROUP_JOCKEY, "05339"), (GROUP_BODY_WEIGHT, (480, 2))])
def test_timed_groups_write_newer_and_reject_older(group, value):
    assert field_transition(group, "x", value, ctx(inc_at=T1, stored_at=T0)).decision == "write"
    assert field_transition(group, "x", value, ctx(inc_at=T0, stored_at=T0)).decision == "write"
    t = field_transition(group, "x", value, ctx(inc_at=T0, stored_at=T1))
    assert t.decision == "reject" and t.reason == "older_observation"
    # no timestamps on either side → write (nothing to compare)
    assert field_transition(group, "x", value, ctx()).decision == "write"


def test_body_weight_is_kept_as_a_pair():
    assert field_transition(GROUP_BODY_WEIGHT, (480, 2), None, ctx()).decision == "keep"
    assert field_transition(GROUP_BODY_WEIGHT, (480, 2), (None, None), ctx()).decision == "keep"
    with pytest.raises(ValueError):
        field_transition(GROUP_BODY_WEIGHT, (480, 2), 482, ctx())


# --- static groups ---------------------------------------------------------------


@pytest.mark.parametrize(
    "group", [GROUP_ENTRY_STATIC, GROUP_TRAINER, GROUP_RACE_STATIC, GROUP_POST_TIME]
)
def test_static_official_owned_keeps_and_reports_mismatch(group):
    t = field_transition(group, "a", "b", ctx(NK, stored_src=JRA_OFFICIAL))
    assert t.decision == "keep" and t.mismatch and t.reason == "official_owned"
    t = field_transition(group, "a", "a", ctx(NK, stored_src=JRA_OFFICIAL))
    assert t.decision == "keep" and not t.mismatch


@pytest.mark.parametrize(
    "group", [GROUP_ENTRY_STATIC, GROUP_TRAINER, GROUP_RACE_STATIC, GROUP_POST_TIME]
)
def test_static_official_corrects_with_mismatch_flag(group):
    t = field_transition(group, "a", "b", ctx(JRA_OFFICIAL, stored_src=NK))
    assert t.decision == "write" and t.mismatch
    t = field_transition(group, "a", "a", ctx(JRA_OFFICIAL, stored_src=NK))
    assert t.decision == "write" and not t.mismatch  # same value: adoption moves, no mismatch
    t = field_transition(group, None, "a", ctx(JRA_OFFICIAL))
    assert t.decision == "write" and not t.mismatch


@pytest.mark.parametrize(
    "group", [GROUP_ENTRY_STATIC, GROUP_TRAINER, GROUP_RACE_STATIC, GROUP_POST_TIME]
)
def test_static_netkeiba_over_netkeiba_writes(group):
    t = field_transition(group, "a", "b", ctx(NK, stored_src=NK))
    assert t.decision == "write" and t.mismatch
    assert field_transition(group, "a", None, ctx(NK, stored_src=NK)).decision == "keep"


def test_unknown_group_raises():
    with pytest.raises(ValueError):
        field_transition("owner", None, "x", ctx())
