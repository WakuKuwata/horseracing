"""Cross-pool place diagnostic: settlement, the no-structure reference, and λ-invariance."""

from __future__ import annotations

import math

import pytest

from horseracing_eval.cross_pool import (
    PLACE_PAYOUT_RATE,
    PRIMARY_POLICIES,
    PlaceRace,
    _select_all,
    _select_rank,
    evaluate_cross_pool,
    rank_profile,
    score_policy,
)


def _race(rid, day, numbers, q, place_prob, dividends, field_size=None):
    return PlaceRace(rid, day, field_size or len(numbers), tuple(numbers), tuple(q),
                     tuple(place_prob), dividends)


def _flat_pool(n_days=10, races_per_day=8, field=8, payout=PLACE_PAYOUT_RATE):
    """A pool with NO structure: every horse placed with prob 3/8 and paid the fair-minus-takeout
    dividend, so every policy must return exactly ``payout``."""
    races = []
    idx = 0
    for d in range(n_days):
        for i in range(races_per_day):
            numbers = list(range(1, field + 1))
            q = [1.0 / field] * field
            pp = [3.0 / field] * field
            # The placing trio rotates over a GLOBAL counter so every field position places
            # exactly 3/field of the time; a per-day counter would tie placing to rank.
            div = {numbers[(idx + k) % field]: payout / (3.0 / field) for k in range(3)}
            races.append(_race(f"{d}-{i}", f"2026-01-{d + 1:02d}", numbers, q, pp, div))
            idx += 1
    return races


# --- settlement --------------------------------------------------------------------------------

def test_absence_from_the_payout_table_is_the_settlement():
    """A horse with no dividend row did not place — it must score 0, not be skipped."""
    r = _race("r", "2026-01-01", [1, 2, 3, 4, 5, 6, 7, 8],
              [0.4, 0.2, 0.1, 0.08, 0.07, 0.06, 0.05, 0.04],
              [0.9, 0.7, 0.4, 0.3, 0.3, 0.2, 0.1, 0.1],
              {1: 1.5, 2: 2.0, 3: 4.0})
    assert r.payout(1) == 1.5
    assert r.payout(8) == 0.0


def test_roi_is_total_payout_over_total_stake():
    races = [
        _race("a", "2026-01-01", [1, 2], [0.6, 0.4], [0.9, 0.8], {1: 3.0}, field_size=8),
        _race("b", "2026-01-02", [1, 2], [0.6, 0.4], [0.9, 0.8], {2: 5.0}, field_size=8),
    ]
    res = score_policy(races, "all", _select_all, b=50, seed=1)
    assert res.n_bets == 4 and res.n_hits == 2
    assert res.roi == pytest.approx((3.0 + 5.0) / 4)


# --- the reference the whole instrument hangs on -------------------------------------------------

def test_a_structureless_pool_returns_one_minus_takeout_for_every_policy():
    """If the place pool were just the win pool re-expressed, WHICH horses you back cannot matter:
    every policy lands on 1 − takeout. This is the null the rank profile is read against."""
    races = _flat_pool()
    out = evaluate_cross_pool(races, b=200, seed=3)
    for pol in out["primary_policies"]:
        assert pol["roi"] == pytest.approx(PLACE_PAYOUT_RATE, abs=1e-9), pol["name"]
    for row in out["rank_profile"]:
        assert row["roi"] == pytest.approx(PLACE_PAYOUT_RATE, abs=1e-9), row["rank"]


def test_a_mispriced_favourite_shows_up_as_a_rank_1_hump():
    """Overpaying placed favourites must lift rank 1 above the reference and leave others at it."""
    races = _flat_pool()
    boosted = []
    for r in races:
        d = dict(r.dividends)
        fav = r.numbers[0]
        if fav in d:
            d[fav] *= 2.0
        boosted.append(_race(r.race_id, r.day, r.numbers, r.q, r.place_prob, d, r.field_size))
    prof = rank_profile(boosted, b=200, seed=3)
    assert prof[0]["roi"] > PLACE_PAYOUT_RATE * 1.2
    assert prof[3]["roi"] == pytest.approx(PLACE_PAYOUT_RATE, abs=1e-9)


# --- λ-invariance of the primary readout ----------------------------------------------------------

def test_rank_policies_ignore_the_place_probability_scale():
    """Rank policies must not move when the derived place probabilities are rescaled — that is
    what makes the primary readout independent of the Harville stage-discount choice."""
    races = _flat_pool()
    rescaled = [_race(r.race_id, r.day, r.numbers, r.q,
                      tuple(p ** 0.5 for p in r.place_prob), r.dividends, r.field_size)
                for r in races]
    a = rank_profile(races, b=100, seed=5)
    b = rank_profile(rescaled, b=100, seed=5)
    assert [x["roi"] for x in a] == [x["roi"] for x in b]


def test_threshold_policies_do_depend_on_the_scale():
    """Stated explicitly so the secondary readout is never mistaken for λ-free."""
    races = _flat_pool()
    lo = score_policy(races, "t", lambda r: [n for n, p in zip(r.numbers, r.place_prob, strict=True) if p >= 0.3],
                      b=50, seed=1)
    rescaled = [_race(r.race_id, r.day, r.numbers, r.q,
                      tuple(p * 0.5 for p in r.place_prob), r.dividends, r.field_size)
                for r in races]
    hi = score_policy(rescaled, "t",
                      lambda r: [n for n, p in zip(r.numbers, r.place_prob, strict=True) if p >= 0.3],
                      b=50, seed=1)
    assert lo.n_bets != hi.n_bets


# --- edges ---------------------------------------------------------------------------------------

def test_rank_beyond_the_field_selects_nothing():
    r = _race("r", "2026-01-01", [1, 2], [0.6, 0.4], [0.9, 0.8], {1: 2.0}, field_size=8)
    assert _select_rank(r, 5) == []
    res = score_policy([r], "rank5", lambda x: _select_rank(x, 5), b=10, seed=1)
    assert res.n_bets == 0 and math.isnan(res.roi) and res.ci.no_decision


def test_policy_set_is_frozen_and_rank_ordered():
    """The policies are pre-registered: a later edit that reorders or extends them is a change of
    contract, not a tweak."""
    assert [n for n, _ in PRIMARY_POLICIES] == [
        "all_started", "win_rank_1", "win_rank_2", "win_rank_3",
        "win_rank_4", "win_rank_5", "win_rank_6",
    ]


def test_empty_input_fails_closed():
    with pytest.raises(ValueError, match="no eligible races"):
        evaluate_cross_pool([], b=10, seed=1)


# --- paired win vs place (pre-registered) ----------------------------------------------------

from horseracing_eval.cross_pool import paired_win_vs_place  # noqa: E402


def _paired_pool(win_mult=1.0, n_days=10, races_per_day=8, field=8):
    """Same structureless pool, now with a win side: one winner per race paying fair-minus-takeout.
    With ``win_mult`` = 1 both tickets are equally priced, so every ΔROI must be 0."""
    races = []
    idx = 0
    for d in range(n_days):
        for _ in range(races_per_day):
            numbers = list(range(1, field + 1))
            q = [1.0 / field] * field
            pp = [3.0 / field] * field
            placers = [numbers[(idx + k) % field] for k in range(3)]
            winner = placers[0]
            div = {n: PLACE_PAYOUT_RATE / (3.0 / field) for n in placers}
            win_odds = {n: PLACE_PAYOUT_RATE * field * win_mult for n in numbers}
            races.append(PlaceRace(f"{d}-{idx}", f"2026-01-{d + 1:02d}", field,
                                   tuple(numbers), tuple(q), tuple(pp), div,
                                   win_odds, frozenset({winner})))
            idx += 1
    return races


def test_equally_priced_pools_give_zero_paired_difference():
    out = paired_win_vs_place(_paired_pool(win_mult=1.0), b=200, seed=3)
    for row in out["by_rank"]:
        assert row["delta_roi"] == pytest.approx(0.0, abs=1e-9), row["rank"]
        assert row["verdict"] == "NO_DECISION"


def test_a_stingy_win_pool_makes_place_cheaper():
    out = paired_win_vs_place(_paired_pool(win_mult=0.5), b=500, seed=3)
    assert out["by_rank"][0]["delta_roi"] > 0
    assert out["by_rank"][0]["verdict"] == "place_cheaper"


def test_paired_scoring_refuses_to_run_without_the_win_side():
    with pytest.raises(ValueError, match="win side not supplied"):
        paired_win_vs_place(_flat_pool(), b=10, seed=1)


def test_only_the_actual_winner_is_paid_on_the_win_ticket():
    r = PlaceRace("r", "2026-01-01", 8, (1, 2, 3), (0.5, 0.3, 0.2), (0.9, 0.7, 0.5),
                  {1: 1.5, 2: 2.0}, {1: 3.0, 2: 5.0, 3: 9.0}, frozenset({2}))
    assert r.win_payout(2) == 5.0
    assert r.win_payout(1) == 0.0   # placed but did not win
    assert r.win_payout(3) == 0.0
