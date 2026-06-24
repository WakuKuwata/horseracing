"""Selection serialization + per-bet-type hit matching (research.md R2/R3, data-model.md §3/§4).

``selection`` is a plain JSON array of horse_numbers (JSONB-safe, NO frozenset/tuple). Ordered
bet types (exacta/trifecta) preserve finishing order; unordered (quinella/wide/trio) are sorted
ascending; place is a single-element array. Order-ness is derived from ``bet_type``.

Hit matching reads the finishing positions only (leak boundary: results enter at SCORING, never
at selection). Inclusion bets (place/wide) use the same field-size rule as the 009 engine, on the
CANONICAL field_size used at generation. Dead-heat that makes a required exact rank ambiguous
returns None for ordered/set bets (caller skips + audits); place/wide score in-range dead-heat as
a hit (inclusion needs no rank uniqueness).
"""

from __future__ import annotations

from collections.abc import Iterable

from horseracing_db.enums import BetType

from .exotic_types import ORDERED_BET_TYPES


def to_selection(bet_type: str, key) -> list[int]:
    """009/010 engine key (int / tuple / frozenset) -> JSONB-safe array of horse_numbers."""
    if bet_type == BetType.PLACE:
        # key is a single horse_number
        return [int(key)]
    if bet_type in ORDERED_BET_TYPES:
        # tuple, finishing order preserved
        return [int(x) for x in key]
    # unordered set -> ascending sort (canonical)
    return sorted(int(x) for x in key)


def selection_key(bet_type: str, selection: Iterable[int]) -> str:
    """Deterministic tie-break string. Ordered-ness already baked into the array order."""
    return f"{bet_type}:" + "-".join(str(int(x)) for x in selection)


def place_top_n(field_size: int) -> int:
    """JRA place/wide payout depth by field size (009 rule): 8+ -> top3, 5-7 -> top2, ≤4 -> 0."""
    if field_size >= 8:
        return 3
    if field_size >= 5:
        return 2
    return 0


def _rank_to_horses(finish_pos: dict[int, int]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for horse, rank in finish_pos.items():
        out.setdefault(rank, []).append(horse)
    return out


def _exact_top(finish_pos: dict[int, int], n: int) -> list[int] | None:
    """The horses finishing ranks 1..n, or None if any of those ranks is dead-heated/missing."""
    by_rank = _rank_to_horses(finish_pos)
    top: list[int] = []
    for r in range(1, n + 1):
        horses = by_rank.get(r)
        if horses is None or len(horses) != 1:
            return None  # ambiguous (dead-heat) or missing -> not scoreable for ordered/set bets
        top.append(horses[0])
    return top


def is_hit(bet_type: str, selection: list[int], finish_pos: dict[int, int], *, field_size: int):
    """Return True/False, or None when the race is not scoreable for this ordered/set bet.

    finish_pos: horse_number -> finishing rank (1-based), finished horses only.
    """
    sel = [int(x) for x in selection]

    if bet_type == BetType.PLACE:
        n = place_top_n(field_size)
        if n == 0:
            return None  # no place bet for ≤4 runners
        rank = finish_pos.get(sel[0])
        return rank is not None and rank <= n  # in-range dead-heat counts (inclusion)

    if bet_type == BetType.WIDE:
        n = place_top_n(field_size)
        if n == 0:
            return None
        return all((finish_pos.get(h) is not None and finish_pos[h] <= n) for h in sel)

    if bet_type == BetType.EXACTA:
        top = _exact_top(finish_pos, 2)
        return None if top is None else (sel == top)

    if bet_type == BetType.QUINELLA:
        top = _exact_top(finish_pos, 2)
        return None if top is None else (set(sel) == set(top))

    if bet_type == BetType.TRIFECTA:
        top = _exact_top(finish_pos, 3)
        return None if top is None else (sel == top)

    if bet_type == BetType.TRIO:
        top = _exact_top(finish_pos, 3)
        return None if top is None else (set(sel) == set(top))

    raise ValueError(f"unsupported exotic bet_type: {bet_type}")
