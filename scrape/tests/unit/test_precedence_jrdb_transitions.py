"""178 T040: contracts/precedence-jrdb.md §3 — the JRDB rows of the transition table, the odds
refusal, the payout confirmation rule and the 速報版 gate; the 176 rows are untouched."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, Source

from horseracing_scrape import precedence as P

NK, JRA, JRDB = Source.NETKEIBA, P.JRA_OFFICIAL, P.JRDB
T0 = datetime.datetime(2026, 11, 7, 8, 0, tzinfo=datetime.UTC)
T1 = T0 + datetime.timedelta(minutes=10)


def ctx(src, stored_src=None, inc_at=None, stored_at=None, page=P.PAGE_ENTRIES):
    return P.TransitionContext(src, inc_at, page, stored_src, stored_at)


@pytest.mark.parametrize(
    "group", [P.GROUP_ENTRY_STATIC, P.GROUP_RACE_STATIC, P.GROUP_POST_TIME, P.GROUP_TRAINER]
)
class TestStaticRows:
    def test_jrdb_writes_when_nothing_is_stored(self, group):
        t = P.field_transition(group, None, "x", ctx(JRDB))
        assert (t.decision, t.mismatch) == ("write", False)

    def test_jrdb_keeps_against_official_owned_value(self, group):
        t = P.field_transition(group, "a", "b", ctx(JRDB, stored_src=JRA))
        assert (t.decision, t.mismatch, t.reason) == ("keep", True, "official_owned")
        same = P.field_transition(group, "a", "a", ctx(JRDB, stored_src=JRA))
        assert (same.decision, same.mismatch) == ("keep", False)

    def test_jrdb_overwrites_netkeiba_with_a_mismatch(self, group):
        t = P.field_transition(group, "a", "b", ctx(JRDB, stored_src=NK))
        assert (t.decision, t.mismatch) == ("write", True)

    def test_netkeiba_keeps_against_jrdb_owned_value(self, group):
        t = P.field_transition(group, "a", "b", ctx(NK, stored_src=JRDB))
        assert (t.decision, t.mismatch, t.reason) == ("keep", True, "jrdb_owned")

    def test_official_overwrites_jrdb(self, group):
        t = P.field_transition(group, "a", "b", ctx(JRA, stored_src=JRDB))
        assert (t.decision, t.mismatch) == ("write", True)

    def test_176_rows_are_unchanged(self, group):
        assert P.field_transition(group, "a", "b", ctx(NK, stored_src=JRA)).decision == "keep"
        assert P.field_transition(group, "a", "b", ctx(JRA, stored_src=NK)).decision == "write"
        assert P.field_transition(group, "a", "b", ctx(NK, stored_src=NK)).decision == "write"
        assert P.field_transition(group, "a", "b", ctx(JRA, stored_src=JRA)).decision == "write"


def test_jockey_is_timed_and_unresolved_person_is_kept():
    assert (
        P.field_transition(
            P.GROUP_JOCKEY, "01019", "05339", ctx(JRDB, inc_at=T1, stored_at=T0)
        ).decision
        == "write"
    )
    assert (
        P.field_transition(
            P.GROUP_JOCKEY, "01019", "05339", ctx(JRDB, inc_at=T0, stored_at=T1)
        ).decision
        == "reject"
    )
    # an unresolved JRDB code arrives as None → keep (the column stays as stored, FR-003a)
    assert (
        P.field_transition(P.GROUP_JOCKEY, "01019", None, ctx(JRDB, inc_at=T1)).decision == "keep"
    )
    assert P.field_transition(P.GROUP_TRAINER, None, None, ctx(JRDB)).decision == "keep"


def test_entry_status_one_way_from_jrdb():
    S, C = EntryStatus.STARTED, EntryStatus.CANCELLED
    assert P.field_transition(P.GROUP_ENTRY_STATUS, S, C, ctx(JRDB)).decision == "write"
    assert P.field_transition(P.GROUP_ENTRY_STATUS, C, S, ctx(JRDB)).decision == "reject"
    assert (
        P.field_transition(P.GROUP_ENTRY_STATUS, S, C, ctx(JRDB, page=P.PAGE_RESULTS)).decision
        == "write"
    )


def test_odds_from_jrdb_are_always_skipped():
    snap = P.OddsSnapshot(T0, None, {1: 2.0, 2: 3.0})
    for kind in (P.KIND_WIN, P.KIND_PLACE):
        for has_results in (False, True):
            d = P.odds_decision(
                kind,
                P.OddsSnapshot(None, None),
                P.OddsSnapshot(T1, JRDB, {1: 2.1, 2: 3.1}),
                [1, 2],
                has_results=has_results,
            )
            assert (d.action, d.reason) == ("skip", "source_not_allowed")
    # the 176 decision for the same inputs is unchanged
    d = P.odds_decision(
        P.KIND_WIN, snap, P.OddsSnapshot(T1, JRA, {1: 2.1, 2: 3.1}), [1, 2], has_results=False
    )
    assert d.action == "write" and d.adopted_source == JRA


def test_payout_jrdb_over_official_same_value_confirms_nothing():
    d = P.payout_block_decision(
        "complete", {(12,): 460}, {(12,): 460}, stored_source=JRA, incoming_source=JRDB
    )
    assert d.action == "replace" and d.to_confirm == () and d.to_update == () and d.to_delete == ()
    # a differing JRDB value still replaces (the official row may be the stale one)
    d = P.payout_block_decision(
        "complete", {(12,): 460}, {(12,): 470}, stored_source=JRA, incoming_source=JRDB
    )
    assert d.to_update == ((12,),)
    # over netkeiba rows JRDB confirms (source moves), as the official route does
    d = P.payout_block_decision(
        "complete", {(12,): 460}, {(12,): 460}, stored_source=NK, incoming_source=JRDB
    )
    assert d.to_confirm == ((12,),)
    # 176 rows unchanged
    d = P.payout_block_decision(
        "complete", {(12,): 460}, {(12,): 460}, stored_source=JRA, incoming_source=NK
    )
    assert d.action == "skip"
    d = P.payout_block_decision(
        "complete", {(12,): 460}, {(12,): 460}, stored_source=NK, incoming_source=JRA
    )
    assert d.to_confirm == ((12,),)


def test_results_input_only_from_the_formal_sed():
    assert P.results_input_allowed(JRDB, "formal") == (True, "ok")
    assert P.results_input_allowed(JRDB, "sokuho") == (False, "sokuho_not_input")
    assert P.results_input_allowed(JRDB, None) == (False, "sokuho_not_input")
    assert P.results_input_allowed(JRA, None) == (True, "ok")
    assert P.results_input_allowed(NK, None) == (True, "ok")
