"""176 T014: contracts/precedence.md §4 — the odds decision table (both kinds, both routes)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import Source

from horseracing_scrape.precedence import (
    JRA_OFFICIAL,
    KIND_PLACE,
    KIND_WIN,
    OddsSnapshot,
    odds_decision,
)

NK = Source.NETKEIBA
T0 = datetime.datetime(2026, 11, 1, 8, 22, tzinfo=datetime.UTC)
T1 = datetime.datetime(2026, 11, 1, 8, 30, tzinfo=datetime.UTC)
STARTED = (1, 2, 3)
V = {1: 2.7, 2: 216.0, 3: 5.1}
V2 = {1: 2.8, 2: 216.0, 3: 5.1}


def snap(at, src, values=V):
    return OddsSnapshot(at, src, values)


@pytest.mark.parametrize("kind", [KIND_WIN, KIND_PLACE])
def test_results_settled(kind):
    d = odds_decision(kind, snap(T0, NK), snap(T1, NK), STARTED, has_results=True)
    if kind == KIND_WIN:
        assert d.action == "settled_fill"
    else:
        assert d.action == "skip"
    # the official route never fills after results
    d = odds_decision(kind, snap(T0, NK), snap(T1, JRA_OFFICIAL), STARTED, has_results=True)
    assert d.action == "skip" and d.reason == "results_settled"


def test_partial_field_skips_whole_race():
    d = odds_decision(
        KIND_WIN,
        snap(None, None, {}),
        snap(T0, JRA_OFFICIAL, {1: 2.7, 2: 216.0}),
        STARTED,
        has_results=False,
    )
    assert d.action == "skip" and d.reason == "partial_field"
    # an empty started set can never be "covered"
    d = odds_decision(KIND_WIN, snap(None, None, {}), snap(T0, JRA_OFFICIAL), (), has_results=False)
    assert d.action == "skip" and d.reason == "partial_field"


def test_no_official_at_with_stored_time_skips():
    d = odds_decision(KIND_WIN, snap(T0, NK), snap(None, NK), STARTED, has_results=False)
    assert d.action == "skip" and d.reason == "no_official_at"


def test_nothing_stored_writes_even_without_time():
    d = odds_decision(KIND_WIN, snap(None, None, {}), snap(None, NK), STARTED, has_results=False)
    assert d.action == "write" and d.adopted_source == NK


def test_stale_skips():
    d = odds_decision(KIND_WIN, snap(T1, NK), snap(T0, JRA_OFFICIAL), STARTED, has_results=False)
    assert d.action == "skip" and d.reason == "stale"


def test_newer_writes_from_either_route():
    for src in (NK, JRA_OFFICIAL):
        d = odds_decision(
            KIND_PLACE, snap(T0, JRA_OFFICIAL), snap(T1, src), STARTED, has_results=False
        )
        assert d.action == "write" and d.adopted_source == src and not d.conflict


def test_same_time_same_value_keeps_official_priority():
    # official → same-time same-value netkeiba: write (observation refresh) but adoption stays
    d = odds_decision(KIND_WIN, snap(T0, JRA_OFFICIAL), snap(T0, NK), STARTED, has_results=False)
    assert d.action == "write" and d.adopted_source == JRA_OFFICIAL and not d.conflict
    # netkeiba → same-time same-value official: adoption moves to official
    d = odds_decision(KIND_WIN, snap(T0, NK), snap(T0, JRA_OFFICIAL), STARTED, has_results=False)
    assert d.action == "write" and d.adopted_source == JRA_OFFICIAL
    # netkeiba → netkeiba
    d = odds_decision(KIND_WIN, snap(T0, NK), snap(T0, NK), STARTED, has_results=False)
    assert d.action == "write" and d.adopted_source == NK


def test_same_time_conflict():
    d = odds_decision(
        KIND_WIN, snap(T0, NK), snap(T0, JRA_OFFICIAL, V2), STARTED, has_results=False
    )
    assert (d.action, d.conflict, d.adopted_source) == ("write", True, JRA_OFFICIAL)
    d = odds_decision(
        KIND_WIN, snap(T0, JRA_OFFICIAL), snap(T0, NK, V2), STARTED, has_results=False
    )
    assert (d.action, d.conflict) == ("skip", True)
    d = odds_decision(KIND_WIN, snap(T0, NK), snap(T0, NK, V2), STARTED, has_results=False)
    assert (d.action, d.conflict, d.adopted_source) == ("write", False, NK)


def test_official_then_same_value_netkeiba_then_conflicting_netkeiba():
    """codex Q2-5: the same-value confirmation must not hand the adoption back to netkeiba."""
    stored = snap(T0, JRA_OFFICIAL)
    d1 = odds_decision(KIND_WIN, stored, snap(T0, NK), STARTED, has_results=False)
    assert d1.adopted_source == JRA_OFFICIAL
    stored2 = OddsSnapshot(T0, d1.adopted_source, V)  # what the writer records
    d2 = odds_decision(KIND_WIN, stored2, snap(T0, NK, V2), STARTED, has_results=False)
    assert d2.action == "skip" and d2.conflict


def test_place_uses_the_same_table():
    d = odds_decision(KIND_PLACE, snap(T0, NK), snap(None, NK), STARTED, has_results=False)
    assert d.action == "skip" and d.reason == "no_official_at"


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        odds_decision("quinella", snap(T0, NK), snap(T1, NK), STARTED, has_results=False)
