"""Pure helpers for determining whether a JRA bet hit.

The API owns this definitional implementation so that its read boundary never imports the
``betting`` package.  All result data is supplied by the caller; these helpers perform no I/O.
"""

from __future__ import annotations

_ORDERED_SIZES = {"exacta": 2, "trifecta": 3}
_UNORDERED_DEPTHS = {"quinella": 2, "trio": 3}


def _is_possible_ordered_finish(
    selection: list[int], finish_order_by_number: dict[int, int], size: int
) -> bool:
    """Return whether ``selection`` can occupy the first ``size`` slots under dead heats."""
    if len(selection) != size or len(set(selection)) != size:
        return False

    ranks = [finish_order_by_number.get(number) for number in selection]
    if any(rank is None for rank in ranks):
        return False

    concrete_ranks = [rank for rank in ranks if rank is not None]
    if concrete_ranks != sorted(concrete_ranks):
        return False

    boundary_rank = concrete_ranks[-1]
    horses_strictly_ahead = {
        number for number, rank in finish_order_by_number.items() if rank < boundary_rank
    }
    return horses_strictly_ahead.issubset(selection)


def _top_finishers(finish_order_by_number: dict[int, int], depth: int) -> set[int]:
    """Return every horse whose official rank is within ``depth``, including dead heats."""
    return {
        number for number, rank in finish_order_by_number.items() if 1 <= rank <= depth
    }


def is_hit(
    bet_type: str,
    selection: list[int],
    finish_order_by_number: dict[int, int],
    n_started: int,
) -> bool | None:
    """Determine whether one canonical selection hit from official finishing positions.

    ``selection`` preserves order for exacta/trifecta and is ascending for unordered bet types.
    ``finish_order_by_number`` contains finished horses only and may assign one rank to multiple
    horses.  Place is unavailable for four or fewer starters and returns ``None`` in that case.
    """
    if bet_type == "place":
        if n_started <= 4:
            return None
        if len(selection) != 1:
            return False
        depth = 2 if n_started <= 7 else 3
        return selection[0] in _top_finishers(finish_order_by_number, depth)

    if bet_type == "win":
        return len(selection) == 1 and finish_order_by_number.get(selection[0]) == 1

    if bet_type == "wide":
        if len(selection) != 2 or len(set(selection)) != 2:
            return False
        return set(selection).issubset(_top_finishers(finish_order_by_number, 3))

    if bet_type in _ORDERED_SIZES:
        return _is_possible_ordered_finish(
            selection, finish_order_by_number, _ORDERED_SIZES[bet_type]
        )

    if bet_type in _UNORDERED_DEPTHS:
        # NOT set-equality with the top-N finishers: a dead heat AT the boundary expands that
        # set beyond N (ranks 1,2,3,3 put four horses inside rank<=3) and JRA then pays EVERY
        # N-horse combination that can occupy the slots ({1,2,3a} and {1,2,3b}), none of which
        # equals the 4-horse set. The correct predicate is the unordered form of the ordered
        # check: right size, all finished, and no non-selected horse strictly ahead of the
        # selection's worst rank (such a horse would have to occupy one of the slots).
        depth = _UNORDERED_DEPTHS[bet_type]
        if len(selection) != depth or len(set(selection)) != depth:
            return False
        ranks = [finish_order_by_number.get(number) for number in selection]
        if any(rank is None for rank in ranks):
            return False
        boundary = max(rank for rank in ranks if rank is not None)
        ahead = {
            number for number, rank in finish_order_by_number.items() if rank < boundary
        }
        return ahead.issubset(set(selection))

    raise ValueError(f"unsupported bet_type: {bet_type}")
