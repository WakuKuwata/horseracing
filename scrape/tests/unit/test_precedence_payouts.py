"""176 T015: contracts/precedence.md §6 — payout blocks are replaced as SETS, per bet type."""

from __future__ import annotations

import pytest
from horseracing_db.enums import Source

from horseracing_scrape.precedence import JRA_OFFICIAL, payout_block_decision

NK = Source.NETKEIBA


def test_complete_replaces_the_set():
    stored = {(12,): 460, (7,): 999}  # stale winner 7 from an earlier (wrong) scrape
    incoming = {(12,): 460, (11,): 460}  # dead heat: two winners
    d = payout_block_decision(
        "complete", stored, incoming, stored_source=NK, incoming_source=JRA_OFFICIAL
    )
    assert d.action == "replace"
    assert d.to_delete == ((7,),)
    assert d.to_update == ((11,),)
    assert d.to_confirm == ((12,),)  # same value: only source/provenance move


def test_same_value_official_adoption_updates_source_only():
    d = payout_block_decision(
        "complete", {(12,): 460}, {(12,): 460}, stored_source=NK, incoming_source=JRA_OFFICIAL
    )
    assert (
        d.action == "replace"
        and d.to_update == ()
        and d.to_delete == ()
        and d.to_confirm == ((12,),)
    )


def test_not_published_does_not_touch_existing_rows():
    d = payout_block_decision(
        "not_published", {(12,): 460}, {}, stored_source=NK, incoming_source=JRA_OFFICIAL
    )
    assert d.action == "noop"


@pytest.mark.parametrize("state", ["not_sold", "refund_only"])
def test_not_sold_and_refund_only_are_reported_not_written(state):
    d = payout_block_decision(
        state, {(12,): 460}, {}, stored_source=NK, incoming_source=JRA_OFFICIAL
    )
    assert d.action == "report" and d.reason == state


def test_parse_failed_skips_only_that_bet_type():
    d = payout_block_decision(
        "parse_failed", {}, {}, stored_source=None, incoming_source=JRA_OFFICIAL
    )
    assert d.action == "skip" and d.reason == "parse_failed"


def test_netkeiba_cannot_revert_an_official_block():
    d = payout_block_decision(
        "complete", {(12,): 460}, {(12,): 450}, stored_source=JRA_OFFICIAL, incoming_source=NK
    )
    assert d.action == "skip" and d.reason == "skipped_official_payouts"


def test_complete_but_empty_is_a_skip_not_a_wipe():
    d = payout_block_decision(
        "complete", {(12,): 460}, {}, stored_source=NK, incoming_source=JRA_OFFICIAL
    )
    assert d.action == "skip" and d.to_delete == ()


def test_unknown_state_raises():
    with pytest.raises(ValueError):
        payout_block_decision("weird", {}, {}, stored_source=None, incoming_source=NK)
