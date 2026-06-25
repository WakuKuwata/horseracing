"""Canonical exotic-bet selection serialization (single source of truth, Feature 012).

The SAME canonicalization Feature 011's ``betting.exotic_selection.to_selection`` uses, lifted into
``db`` so both ``scrape`` (writing exotic_odds) and ``betting`` (reading/matching) agree on the
JSONB-safe selection array — a parity test guards the two implementations. Ordered bets
(exacta/trifecta) preserve finishing order; unordered (quinella/wide/trio) are ascending-sorted;
place is a single-element array. The result joins exotic_odds ↔ recommendations by exact equality.
"""

from __future__ import annotations

from collections.abc import Iterable

from .enums import BetType

#: bet types whose selection preserves order (others are set-style / inclusion).
ORDERED_BET_TYPES: frozenset[str] = frozenset({BetType.EXACTA, BetType.TRIFECTA})


def canonical_selection(bet_type: str, numbers: Iterable[int]) -> list[int]:
    """horse_numbers -> JSONB-safe array (ordered kept / unordered sorted / place single [i])."""
    nums = [int(n) for n in numbers]
    if bet_type == BetType.PLACE:
        if len(nums) != 1:
            raise ValueError(f"place selection must be a single horse_number, got {nums}")
        return [nums[0]]
    if bet_type in ORDERED_BET_TYPES:
        return nums  # finishing order preserved
    if bet_type in (BetType.QUINELLA, BetType.WIDE, BetType.TRIO):
        return sorted(nums)  # ascending canonical
    raise ValueError(f"unsupported exotic bet_type: {bet_type}")
