"""Joint-probability consistency checks (contracts/engine.md, FR-006, INV-J2..J5).

Fail-fast verification that an engine output is internally consistent AND that its joint marginals
agree with the independently-implemented harville_topk (the strongest correctness evidence).
"""

from __future__ import annotations

from horseracing_eval.baselines import harville_topk

from .engine import JointProbabilities

DEFAULT_TOL = {"sum": 1e-6, "marginal": 1e-6, "range": 1e-9}


class JointConsistencyError(ValueError):
    """Raised when joint probabilities violate a consistency invariant."""


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def check_joint_consistency(
    jp: JointProbabilities, tol: dict[str, float] | None = None
) -> None:
    t = {**DEFAULT_TOL, **(tol or {})}
    ids = sorted(jp.win)
    p = [jp.win[h] for h in ids]
    n = len(ids)

    # Σexacta = 1
    s_ex = sum(jp.exacta.values())
    if not _close(s_ex, 1.0, t["sum"]):
        raise JointConsistencyError(f"Σexacta={s_ex} != 1")

    # quinella = exacta(i,j)+exacta(j,i); wide >= quinella
    for key, q in jp.quinella.items():
        i, j = tuple(key)
        if not _close(q, jp.exacta[(i, j)] + jp.exacta[(j, i)], t["sum"]):
            raise JointConsistencyError(f"quinella {key} != exacta sum")
        if jp.wide is not None and jp.wide[key] < q - t["range"]:
            raise JointConsistencyError(f"wide < quinella for {key}")

    # exacta marginal == harville top2
    top2, top3 = harville_topk(p)
    for idx, h in enumerate(ids):
        marg2 = sum(jp.exacta[(h, o)] + jp.exacta[(o, h)] for o in ids if o != h)
        if not _close(marg2, top2[idx], t["marginal"]):
            raise JointConsistencyError(f"exacta marginal {h}={marg2} != harville top2 {top2[idx]}")

    if n >= 3:
        s_tri = sum(jp.trifecta.values())
        if not _close(s_tri, 1.0, t["sum"]):
            raise JointConsistencyError(f"Σtrifecta={s_tri} != 1")
        # trifecta marginal (ordered triples containing h) == harville top3
        for idx, h in enumerate(ids):
            marg3 = sum(v for trip, v in jp.trifecta.items() if h in trip)
            if not _close(marg3, top3[idx], t["marginal"]):
                raise JointConsistencyError(
                    f"trifecta marginal {h}={marg3} != harville top3 {top3[idx]}"
                )
        for key, v in jp.trio.items():
            if not (-t["range"] <= v <= 1.0 + t["range"]):
                raise JointConsistencyError(f"trio {key}={v} out of [0,1]")

    # place range + monotonicity
    if jp.place is not None:
        for h, v in jp.place.items():
            if not (-t["range"] <= v <= 1.0 + t["range"]):
                raise JointConsistencyError(f"place {h}={v} out of [0,1]")
        order = sorted(ids, key=lambda h: jp.win[h], reverse=True)
        for a, b in zip(order, order[1:], strict=False):
            if jp.place[a] < jp.place[b] - t["marginal"]:
                raise JointConsistencyError(f"place not monotone: {a}<{b}")
