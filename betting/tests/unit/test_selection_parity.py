"""T006 (012): db.canonical_selection MUST equal 011 betting.to_selection for all bet types.

Guards the exotic_odds ↔ recommendations / estimated-odds join: both sides must produce the
identical JSONB-safe selection array, or real-vs-estimated matching silently misses (SC-004).

Lives in betting/ (not db/) because betting depends on db, so both are importable here.
"""

from __future__ import annotations

from horseracing_db.enums import BetType
from horseracing_db.selection import canonical_selection

from horseracing_betting.exotic_selection import to_selection


def test_place_parity():
    assert canonical_selection(BetType.PLACE, [5]) == to_selection(BetType.PLACE, 5) == [5]


def test_ordered_parity_preserves_order():
    assert canonical_selection(BetType.EXACTA, [7, 3]) == to_selection(BetType.EXACTA, (7, 3))
    assert canonical_selection(BetType.TRIFECTA, [3, 7, 1]) == to_selection(
        BetType.TRIFECTA, (3, 7, 1)
    )


def test_unordered_parity_sorts_ascending():
    assert canonical_selection(BetType.QUINELLA, [7, 3]) == to_selection(
        BetType.QUINELLA, frozenset({7, 3})
    )
    assert canonical_selection(BetType.WIDE, [9, 2]) == to_selection(BetType.WIDE, frozenset({9, 2}))
    assert canonical_selection(BetType.TRIO, [7, 1, 3]) == to_selection(
        BetType.TRIO, frozenset({7, 1, 3})
    )


def test_unordered_input_order_irrelevant():
    # any input ordering of an unordered bet canonicalizes identically
    assert canonical_selection(BetType.TRIO, [7, 3, 1]) == canonical_selection(
        BetType.TRIO, [1, 7, 3]
    ) == [1, 3, 7]


def test_all_exotic_bet_types_covered():
    for bt in BetType.EXOTIC:
        n = [1] if bt == BetType.PLACE else ([1, 2] if bt in ("quinella", "exacta", "wide") else [1, 2, 3])
        assert isinstance(canonical_selection(bt, n), list)
