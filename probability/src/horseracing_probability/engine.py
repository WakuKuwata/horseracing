"""Joint probability engine (contracts/engine.md, INV-J1..J8).

joint_probabilities(win_probs) derives all 7 JRA bet-type probabilities from per-race win probs
via Plackett-Luce (sampling without replacement). The caller passes the RUNNING field (scratched
already excluded); the engine renormalizes to Σ=1, clips to [eps,1-eps], renormalizes again, then
derives — renormalization happens BEFORE the PL denominators so Σexacta=Σtrifecta=1 hold exactly
(codex). Unordered = sum of orderings; wide{i,j}=Σ_k trio{i,j,k} (NOT top3_i·top3_j); place =
harville top-N (field-size dependent). harville's denominator-skip is NOT inherited (clip handles
endpoints), so joint marginals match harville_topk for the same normalized vector.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from horseracing_eval.baselines import harville_topk

DEFAULT_EPS = 1e-9


@dataclass(frozen=True)
class JointProbabilities:
    win: dict[str, float]
    place: dict[str, float] | None
    exacta: dict[tuple[str, str], float]
    quinella: dict[frozenset[str], float]
    wide: dict[frozenset[str], float] | None
    trifecta: dict[tuple[str, str, str], float]
    trio: dict[frozenset[str], float]


def _normalize_clip(win_probs: dict[str, float], eps: float) -> tuple[list[str], list[float]]:
    if not win_probs:
        raise ValueError("empty win_probs")
    ids = sorted(win_probs)  # deterministic horse order
    p = [float(win_probs[h]) for h in ids]
    total = sum(p)
    if total <= 0.0:
        raise ValueError("win probabilities sum to <= 0")
    p = [x / total for x in p]                      # renormalize BEFORE denominators
    p = [min(max(x, eps), 1.0 - eps) for x in p]    # clip endpoints
    total = sum(p)
    p = [x / total for x in p]                       # renormalize after clip
    return ids, p


def _place(ids: list[str], p: list[float], field_size: int) -> dict[str, float] | None:
    if field_size <= 4:  # JRA: no place bet for <=4 runners
        return None
    top2, top3 = harville_topk(p)
    incl = top2 if field_size <= 7 else top3        # 5-7 -> top2, 8+ -> top3
    return {ids[i]: incl[i] for i in range(len(ids))}


def joint_probabilities(
    win_probs: dict[str, float], *, field_size: int | None = None, eps: float = DEFAULT_EPS
) -> JointProbabilities:
    ids, p = _normalize_clip(win_probs, eps)
    n = len(ids)
    rng = range(n)
    win = {ids[i]: p[i] for i in rng}

    # exacta (ordered pairs) + quinella (unordered = both orderings)
    exacta: dict[tuple[str, str], float] = {}
    for i in rng:
        di = 1.0 - p[i]
        for j in rng:
            if j == i:
                continue
            exacta[(ids[i], ids[j])] = p[i] * p[j] / di
    quinella: dict[frozenset[str], float] = {}
    for i in rng:
        for j in range(i + 1, n):
            quinella[frozenset((ids[i], ids[j]))] = (
                exacta[(ids[i], ids[j])] + exacta[(ids[j], ids[i])]
            )

    # trifecta (ordered triples) + trio (unordered = 6 orderings); N<3 -> empty
    trifecta: dict[tuple[str, str, str], float] = {}
    trio: dict[frozenset[str], float] = {}
    wide: dict[frozenset[str], float] | None = None
    if n >= 3:
        for i in rng:
            di = 1.0 - p[i]
            for j in rng:
                if j == i:
                    continue
                dij = 1.0 - p[i] - p[j]
                if dij < eps:  # floor (post-normalize this is > 0; defensive)
                    dij = eps
                for k in rng:
                    if k == i or k == j:
                        continue
                    trifecta[(ids[i], ids[j], ids[k])] = p[i] * (p[j] / di) * (p[k] / dij)
        for a, b, c in itertools.combinations(rng, 3):
            s = 0.0
            for perm in itertools.permutations((a, b, c)):
                s += trifecta[(ids[perm[0]], ids[perm[1]], ids[perm[2]])]
            trio[frozenset((ids[a], ids[b], ids[c]))] = s
        # wide{i,j} = P(both in top3) = Σ_k trio{i,j,k}
        wide = {}
        for i in rng:
            for j in range(i + 1, n):
                w = 0.0
                for k in rng:
                    if k == i or k == j:
                        continue
                    w += trio[frozenset((ids[i], ids[j], ids[k]))]
                wide[frozenset((ids[i], ids[j]))] = w

    place = _place(ids, p, field_size if field_size is not None else n)
    return JointProbabilities(
        win=win, place=place, exacta=exacta, quinella=quinella, wide=wide,
        trifecta=trifecta, trio=trio,
    )
