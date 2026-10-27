"""Official win settlement is shared by actual purchases and frozen-choice comparisons."""
from copy import deepcopy

import pytest

from horseracing_api.purchase_view import (
    PENDING,
    REFUNDED,
    SETTLED_REAL,
    UNSETTLEABLE,
    build_result_context,
    settle_bet,
    settle_policy_net,
)


def _facts():
    entries = [dict(horse_id=f"h{n}", horse_number=n, entry_status="started", odds=odds)
               for n, odds in [(1, 26.5), (2, 39.6), (3, 12.0)]]
    results = [dict(horse_id=f"h{n}", result_status="finished", finish_order=n)
               for n in range(1, 4)]
    payouts = [dict(horse_number=1, payout_yen=1340)]
    return entries, results, payouts


def _ctx(*facts):
    return {**build_result_context(*(facts or _facts())), "dividends": {}}


def _bet(number, amount=100):
    return dict(bet_type="win", selection=[number], amount_yen=amount)


def _snapshot(number, amount=100, odds=999.9):
    return {"bets": [{**_bet(number, amount), "odds_used": odds}]}


def test_win_uses_official_payout_even_when_current_and_frozen_odds_differ():
    ctx = _ctx()
    bet = settle_bet(ctx, {**_bet(1, 300), "odds_used": 500.0})
    assert (bet.status, bet.hit, bet.payout_yen, bet.is_estimated) == (
        SETTLED_REAL, True, 4020.0, False)
    assert settle_policy_net(ctx, _snapshot(1, 300, odds=500.0)) == 3720.0
    assert settle_bet(ctx, _bet(3)).payout_yen == 0.0


def test_current_or_frozen_odds_are_not_required_for_official_settlement():
    entries, results, payouts = _facts()
    for h in entries:
        h["odds"] = None
    ctx = _ctx(entries, results, payouts)
    assert settle_bet(ctx, _bet(1)).payout_yen == 1340.0
    assert settle_policy_net(ctx, _snapshot(1, odds=None)) == 1240.0


def test_dead_heat_values_each_winner_at_their_own_official_payout():
    entries, results, payouts = _facts()
    results[1]["finish_order"] = 1
    payouts.append(dict(horse_number=2, payout_yen=1960))
    ctx = _ctx(entries, results, payouts)
    assert settle_bet(ctx, _bet(1)).payout_yen == 1340.0
    assert settle_bet(ctx, _bet(2, 500)).payout_yen == 9800.0
    assert settle_bet(ctx, _bet(3)).hit is False


def test_legitimate_100_yen_payout_returns_the_stake():
    entries, results, payouts = _facts()
    payouts[0]["payout_yen"] = 100
    ctx = _ctx(entries, results, payouts)
    assert settle_bet(ctx, _bet(1, 500)).payout_yen == 500.0
    assert settle_policy_net(ctx, _snapshot(1, 500)) == 0.0


@pytest.mark.parametrize("case,reason", [
    ("no_payout", "win_payout_missing"),
    ("partial_dead_heat", "win_payout_inconsistent"),
    ("extra_loser_payout", "win_payout_inconsistent"),
    ("wrong_winner", "win_payout_inconsistent"),
    ("invalid_payout", "win_payout_inconsistent"),
])
def test_payout_defects_leave_winning_and_losing_bets_unvalued(case, reason):
    entries, results, payouts = _facts()
    if case == "no_payout":
        payouts = []
    elif case == "partial_dead_heat":
        results[1]["finish_order"] = 1
    elif case == "extra_loser_payout":
        payouts.append(dict(horse_number=3, payout_yen=200))
    elif case == "wrong_winner":
        payouts[0]["horse_number"] = 3
    else:
        payouts[0]["payout_yen"] = 70
    ctx = _ctx(entries, results, payouts)
    for n in range(1, 4):
        bet = settle_bet(ctx, _bet(n))
        assert (bet.status, bet.hit, bet.payout_yen, bet.settlement_reason) == (
            UNSETTLEABLE, None, None, reason)
        assert settle_policy_net(ctx, _snapshot(n)) is None


@pytest.mark.parametrize("n", [1, 2, 3])
def test_partial_results_do_not_settle_winner_loser_or_refund_missing_starter(n):
    entries, results, payouts = _facts()
    ctx = _ctx(entries, results[:2], payouts)
    bet = settle_bet(ctx, _bet(n))
    assert (bet.status, bet.hit, bet.payout_yen, bet.settlement_reason) == (
        PENDING, None, None, "results_incomplete")
    assert settle_policy_net(ctx, _snapshot(n)) is None


def test_no_results_stays_pending_even_with_a_payout_present():
    entries, _, payouts = _facts()
    bet = settle_bet(_ctx(entries, [], payouts), _bet(1))
    assert (bet.status, bet.settlement_reason, bet.payout_yen) == (
        PENDING, "results_pending", None)


@pytest.mark.parametrize("status", ["cancelled", "excluded"])
@pytest.mark.parametrize("results_known", [True, False])
def test_only_explicit_non_starters_refund_even_before_other_results(status, results_known):
    entries, results, payouts = _facts()
    entries[2]["entry_status"] = status
    ctx = _ctx(entries, results[:2] if results_known else [], payouts)
    bet = settle_bet(ctx, _bet(3, 300))
    assert (bet.status, bet.hit, bet.payout_yen) == (REFUNDED, None, 300.0)
    assert settle_policy_net(ctx, _snapshot(3, 300)) == 0.0


def test_unknown_number_is_not_a_refund():
    bet = settle_bet(_ctx(), _bet(99))
    assert (bet.status, bet.payout_yen, bet.settlement_reason) == (
        UNSETTLEABLE, None, "selection_unknown")


def test_contradictory_nonstarter_with_a_result_is_not_refunded():
    entries, results, payouts = _facts()
    entries[2]["entry_status"] = "cancelled"
    bet = settle_bet(_ctx(entries, results, payouts), _bet(3))
    assert (bet.status, bet.hit, bet.payout_yen, bet.settlement_reason) == (
        UNSETTLEABLE, None, None, "results_inconsistent")


def test_invalid_multi_horse_win_selection_is_not_refunded():
    entries, results, payouts = _facts()
    entries[2]["entry_status"] = "cancelled"
    bet = settle_bet(_ctx(entries, results[:2], payouts), {
        "bet_type": "win", "selection": [1, 3], "amount_yen": 100})
    assert bet.status == UNSETTLEABLE and bet.payout_yen is None


@pytest.mark.parametrize("status", ["stopped", "disqualified"])
def test_non_finished_starters_are_covered_results_and_losing_win_bets(status):
    entries, results, payouts = _facts()
    results[2].update(result_status=status, finish_order=None)
    ctx = _ctx(entries, results, payouts)
    bet = settle_bet(ctx, _bet(3))
    assert ctx["result_state"] == "complete"
    assert (bet.status, bet.hit, bet.payout_yen) == (SETTLED_REAL, False, 0.0)


@pytest.mark.parametrize("case,reason", [
    ("duplicate_number", "field_inconsistent"),
    ("null_number", "field_inconsistent"),
    ("unknown_entry_status", "field_inconsistent"),
    ("orphan_result", "results_inconsistent"),
    ("nonstarter_result", "results_inconsistent"),
    ("no_winner", "results_inconsistent"),
    ("missing_finished_order", "results_inconsistent"),
    ("unknown_result_status", "results_inconsistent"),
])
def test_invalid_field_and_results_fail_closed(case, reason):
    entries, results, payouts = _facts()
    if case == "duplicate_number":
        entries[2].update(horse_number=1, entry_status="cancelled")
    elif case == "null_number":
        entries[2]["horse_number"] = None
    elif case == "unknown_entry_status":
        entries[2]["entry_status"] = None
    elif case == "orphan_result":
        results.append(dict(horse_id="orphan", finish_order=2, result_status="finished"))
    elif case == "nonstarter_result":
        entries[2]["entry_status"] = "cancelled"
    elif case == "no_winner":
        results[0]["finish_order"] = 2
    elif case == "missing_finished_order":
        results[2]["finish_order"] = None
    else:
        results[2]["result_status"] = None
    bet = settle_bet(_ctx(entries, results, payouts), _bet(1))
    assert (bet.status, bet.hit, bet.payout_yen, bet.settlement_reason) == (
        UNSETTLEABLE, None, None, reason)


def test_policy_preserves_missing_snapshot_and_unknown_amount_instead_of_zero():
    ctx = _ctx()
    assert settle_policy_net(ctx, None) is None
    assert settle_policy_net(ctx, _snapshot(1, None)) is None
    assert settle_policy_net(ctx, {"bets": []}) == 0.0
    assert settle_policy_net(ctx, {"bets": [dict(betType="win", selection=[1], amountYen=200)]}) == 2480.0


def test_settlement_does_not_mutate_context_or_snapshot():
    ctx = _ctx()
    snapshot = _snapshot(1)
    before = deepcopy((ctx, snapshot))
    settle_bet(ctx, _bet(1))
    settle_policy_net(ctx, snapshot)
    assert (ctx, snapshot) == before
