"""T007: selection serialization round-trip + per-bet-type hit matching (SC-003/SC-004)."""

from __future__ import annotations

from horseracing_db.enums import BetType

from horseracing_betting.exotic_selection import (
    is_hit,
    place_top_n,
    selection_key,
    to_selection,
)


def test_to_selection_ordered_preserves_order():
    assert to_selection(BetType.EXACTA, (7, 3)) == [7, 3]
    assert to_selection(BetType.TRIFECTA, (3, 7, 1)) == [3, 7, 1]


def test_to_selection_unordered_sorts_ascending():
    assert to_selection(BetType.QUINELLA, frozenset({7, 3})) == [3, 7]
    assert to_selection(BetType.TRIO, frozenset({7, 1, 3})) == [1, 3, 7]
    assert to_selection(BetType.WIDE, frozenset({9, 2})) == [2, 9]


def test_to_selection_place_single_element():
    assert to_selection(BetType.PLACE, 5) == [5]


def test_selection_is_plain_list_not_frozenset():
    sel = to_selection(BetType.TRIO, frozenset({2, 5, 1}))
    assert isinstance(sel, list)
    assert all(isinstance(x, int) for x in sel)


def test_selection_key_deterministic_and_distinguishes_order():
    assert selection_key(BetType.EXACTA, [7, 3]) != selection_key(BetType.EXACTA, [3, 7])
    assert selection_key(BetType.TRIO, [1, 3, 7]) == selection_key(BetType.TRIO, [1, 3, 7])


def test_place_top_n_field_rule():
    assert place_top_n(10) == 3  # 8+ -> top3
    assert place_top_n(8) == 3
    assert place_top_n(7) == 2   # 5-7 -> top2
    assert place_top_n(5) == 2
    assert place_top_n(4) == 0   # ≤4 -> none


# finish_pos: horse_number -> finishing rank
FP = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8}


def test_exacta_ordered_hit_and_miss():
    assert is_hit(BetType.EXACTA, [1, 2], FP, field_size=8) is True
    assert is_hit(BetType.EXACTA, [2, 1], FP, field_size=8) is False  # order matters


def test_quinella_set_hit_ignores_order():
    assert is_hit(BetType.QUINELLA, [1, 2], FP, field_size=8) is True
    assert is_hit(BetType.QUINELLA, [2, 1], FP, field_size=8) is True
    assert is_hit(BetType.QUINELLA, [1, 3], FP, field_size=8) is False


def test_trifecta_and_trio():
    assert is_hit(BetType.TRIFECTA, [1, 2, 3], FP, field_size=8) is True
    assert is_hit(BetType.TRIFECTA, [1, 3, 2], FP, field_size=8) is False
    assert is_hit(BetType.TRIO, [3, 1, 2], FP, field_size=8) is True
    assert is_hit(BetType.TRIO, [1, 2, 4], FP, field_size=8) is False


def test_wide_inclusion_top3_for_large_field():
    assert is_hit(BetType.WIDE, [1, 3], FP, field_size=8) is True   # both in top3
    assert is_hit(BetType.WIDE, [1, 4], FP, field_size=8) is False  # 4th out of top3


def test_wide_top2_for_small_field():
    assert is_hit(BetType.WIDE, [1, 3], FP, field_size=7) is False  # 5-7 -> top2 only
    assert is_hit(BetType.WIDE, [1, 2], FP, field_size=7) is True


def test_place_field_rule_and_none_for_tiny_field():
    assert is_hit(BetType.PLACE, [3], FP, field_size=8) is True     # top3
    assert is_hit(BetType.PLACE, [4], FP, field_size=8) is False
    assert is_hit(BetType.PLACE, [3], FP, field_size=7) is False    # top2 only
    assert is_hit(BetType.PLACE, [2], FP, field_size=7) is True
    assert is_hit(BetType.PLACE, [1], FP, field_size=4) is None     # ≤4 -> no place bet


def test_dead_heat_ordered_returns_none_but_inclusion_scores():
    # dead-heat for 1st: two horses at rank 1, no unique 2nd either
    dh = {1: 1, 2: 1, 3: 3, 4: 4, 5: 5}
    assert is_hit(BetType.EXACTA, [1, 2], dh, field_size=8) is None   # ambiguous -> skip
    assert is_hit(BetType.QUINELLA, [1, 2], dh, field_size=8) is None
    # wide/place inclusion still scores in-range dead-heat as a hit
    assert is_hit(BetType.WIDE, [1, 2], dh, field_size=8) is True
    assert is_hit(BetType.PLACE, [1], dh, field_size=8) is True
