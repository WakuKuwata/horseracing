"""T008: canonical field keeps ONE shared p/q population (SC-002)."""

from __future__ import annotations

import math

from horseracing_betting.exotic_ev import canonical_field


def test_population_is_intersection_of_valid_p_and_odds():
    preds = {1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1}
    odds = {1: 2.0, 2: 3.5, 3: 6.0, 4: 12.0}
    f = canonical_field("R", preds, odds)
    assert f.horse_numbers == [1, 2, 3, 4]
    assert set(f.p_norm) == set(f.odds_norm) == {1, 2, 3, 4}
    assert math.isclose(sum(f.p_norm.values()), 1.0, abs_tol=1e-12)
    assert f.field_size == 4


def test_prob_only_and_odds_only_horses_excluded():
    preds = {1: 0.4, 2: 0.3, 3: 0.3}      # horse 4 has no prob
    odds = {1: 2.0, 2: 3.5, 4: 9.0}        # horse 3 has no odds
    f = canonical_field("R", preds, odds)
    assert set(f.p_norm) == set(f.odds_norm) == {1, 2}
    reasons = {e.horse_number: e.reason for e in f.excluded}
    assert reasons[3] == "no_odds"
    assert reasons[4] == "no_prob"


def test_renormalization_over_population_sums_to_one():
    preds = {1: 0.4, 2: 0.3, 3: 0.3}
    odds = {1: 2.0, 2: 3.0}                 # 3 dropped -> renorm over {1,2}
    f = canonical_field("R", preds, odds)
    assert math.isclose(sum(f.p_norm.values()), 1.0, abs_tol=1e-12)
    # 0.4 / 0.7 and 0.3 / 0.7
    assert math.isclose(f.p_norm[1], 0.4 / 0.7, abs_tol=1e-9)


def test_scratched_horses_excluded_with_reason():
    preds = {1: 0.5, 2: 0.3, 3: 0.2}
    odds = {1: 2.0, 2: 3.0, 3: 5.0}
    f = canonical_field("R", preds, odds, scratched={3: "cancelled"})
    assert set(f.p_norm) == {1, 2}
    assert any(e.horse_number == 3 and e.reason == "cancelled" for e in f.excluded)


def test_zero_or_negative_values_excluded():
    preds = {1: 0.5, 2: 0.0, 3: 0.5}
    odds = {1: 2.0, 2: 3.0, 3: -1.0}
    f = canonical_field("R", preds, odds)
    # only horse 1 is valid (2 has zero prob, 3 has invalid odds) -> field_size 1, no exotic
    reasons = {e.horse_number: e.reason for e in f.excluded}
    assert reasons[2] == "no_prob" and reasons[3] == "no_odds"
    assert f.field_size == 1
    assert f.p_norm == {} and f.odds_norm == {}  # <2 horses -> empty (no normalization)


def test_empty_population_no_normalization():
    f = canonical_field("R", {}, {})
    assert f.field_size == 0
    assert f.p_norm == {} and f.odds_norm == {}  # no 0-division
