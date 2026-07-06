"""Conditional-logit (race-softmax / Plackett-Luce top-1) objective helpers (Feature 039).

The win structure is "exactly one winner per race" = a multinomial over the race field.
Rather than binary per-horse P(win) + post-hoc 009 renormalization, this objective models
each race directly: score s_i -> race softmax p_i = exp(s_i)/Σ_j exp(s_j), loss = −log p_winner.

All helpers operate on arrays whose rows are grouped contiguously by race (the caller
stable-sorts by race_id and passes group sizes). The objective NEVER reads finishing
position, result-time status, or market prices — only the win label (for the loss) and
race membership (for the softmax normalization unit). Groups with sum(y) != 1 (no winner
/ dead-heat) are neutralized (grad/hess = 0) so a malformed race does not corrupt
learning.
"""

from __future__ import annotations

import numpy as np

_HESS_FLOOR = 1e-6


def group_sizes_from_race_ids(race_ids) -> list[int]:
    """Contiguous run lengths of race_ids (caller must have sorted rows by race).

    Assumes rows are already grouped contiguously (same race_id adjacent). Returns the
    size of each contiguous race block in encounter order.
    """
    arr = np.asarray(race_ids)
    if len(arr) == 0:
        return []
    # boundaries where the race id changes
    change = np.nonzero(arr[1:] != arr[:-1])[0] + 1
    bounds = np.concatenate(([0], change, [len(arr)]))
    return np.diff(bounds).astype(int).tolist()


def race_softmax(scores, group_sizes: list[int]) -> np.ndarray:
    """Per-race softmax over contiguous groups (numerically stable via max subtraction)."""
    scores = np.asarray(scores, dtype=float)
    out = np.empty_like(scores)
    start = 0
    for g in group_sizes:
        sl = slice(start, start + g)
        v = scores[sl]
        v = v - v.max()
        e = np.exp(v)
        out[sl] = e / e.sum()
        start += g
    return out


def cond_logit_objective(group_sizes: list[int]):
    """LightGBM 4.x custom objective: fobj(preds, dataset) -> (grad, hess).

    Per race group: p = softmax(preds); if the group has exactly one winner
    (sum(y)==1), grad = p − y and hess = max(p(1−p), floor) (multinomial diagonal
    Newton approx). Groups with sum(y) != 1 (no winner / dead-heat) are neutralized
    (grad = 0, hess = floor) so they contribute no learning signal.
    """

    def fobj(preds, dataset):
        y = np.asarray(dataset.get_label(), dtype=float)
        preds = np.asarray(preds, dtype=float)
        grad = np.zeros_like(preds)
        hess = np.full_like(preds, _HESS_FLOOR)
        start = 0
        for g in group_sizes:
            sl = slice(start, start + g)
            start += g
            yg = y[sl]
            if abs(yg.sum() - 1.0) > 1e-9:  # no winner or dead-heat -> neutralize
                continue
            v = preds[sl]
            v = v - v.max()
            e = np.exp(v)
            p = e / e.sum()
            grad[sl] = p - yg
            hess[sl] = np.maximum(p * (1.0 - p), _HESS_FLOOR)
        # LightGBM does NOT auto-apply sample weight to custom objectives — do it here.
        # No weight set (e.g. lgbm-039) -> get_weight() is None -> grad/hess unchanged.
        w = dataset.get_weight()
        if w is not None:
            w = np.asarray(w, dtype=float)
            grad *= w
            hess *= w
        return grad, hess

    return fobj


#: Feature 042: stage decay for PL top-k (pre-registered from the spike; NOT tuned on OOS).
#: Later finishing positions are noisier (eased-up finishes), so stages decay.
STAGE_WEIGHTS: tuple[float, ...] = (1.0, 0.5, 0.25)


def _pl_topk_objective_loop(group_sizes: list[int], ranks):
    """Reference (per-group Python loop) PL top-k objective — the correctness ORACLE.

    Retained for the equivalence test and for exact reproduction of models trained before the
    vectorized default (the vectorized version below differs by ~1 ulp on the softmax denominator
    — mathematically identical, not bit-identical; user decision 2026-07-06 accepted this for the
    ~4x speedup, so the vectorized form is the default). Semantics: see ``pl_topk_objective``.
    """
    ranks = np.asarray(ranks)

    def fobj(preds, dataset):
        preds = np.asarray(preds, dtype=float)
        grad = np.zeros_like(preds)
        hess = np.zeros_like(preds)
        start = 0
        for g in group_sizes:
            sl = slice(start, start + g)
            start += g
            rk = ranks[sl]
            if (rk == 1).sum() != 1:  # no unique winner -> neutralize group (039 rule)
                continue
            v = preds[sl]
            remaining = np.ones(g, dtype=bool)
            for j, w in enumerate(STAGE_WEIGHTS, start=1):
                target = rk == j
                if target.sum() != 1 or remaining.sum() < 2:
                    break  # keep earlier stages' gradients
                vr = v[remaining] - v[remaining].max()
                e = np.exp(vr)
                p = e / e.sum()
                yj = target[remaining].astype(float)
                gsub = grad[sl]
                hsub = hess[sl]
                gsub[remaining] += w * (p - yj)
                hsub[remaining] += w * np.maximum(p * (1.0 - p), _HESS_FLOOR)
                grad[sl] = gsub
                hess[sl] = hsub
                remaining = remaining & ~target
        hess = np.maximum(hess, _HESS_FLOOR)
        w_arr = dataset.get_weight()
        if w_arr is not None:
            w_arr = np.asarray(w_arr, dtype=float)
            grad *= w_arr
            hess *= w_arr
        return grad, hess

    return fobj


#: masking sentinel for already-placed horses: exp(NEG − max) underflows to 0.0 (excludes them
#: from the softmax) while staying FINITE, so a non-firing group never yields NaN (−inf − (−inf)).
_NEG_SENTINEL = -1e30


def pl_topk_objective(group_sizes: list[int], ranks):
    """Feature 042: Plackett-Luce top-k (k=len(STAGE_WEIGHTS)) sequential objective.

    ``ranks``: per-row finishing rank (1..k) or 0 (others/DNF), aligned to the sorted rows.
    Stage j softmaxes over the not-yet-placed horses and targets the j-th finisher:
    grad += w_j(p − y_j), hess += w_j·p(1−p) on the remaining set. Stage 1 without a
    unique winner neutralizes the whole group (as cond_logit); a later stage without a
    unique target (dead-heat / missing) or with <2 remaining horses breaks — earlier
    stages' gradients are kept. Sample weight is applied explicitly (LightGBM does not
    auto-apply it to custom objectives); no weight -> unchanged.

    VECTORIZED (Feature perf): ``ranks``/``group_sizes`` are fixed across boosting rounds, so all
    membership/validity/break masks are precomputed ONCE here; each ``fobj`` call (per round) does
    only whole-array softmax arithmetic via segment reductions (no Python per-group loop) — ~4x
    faster (fobj was ~83% of fit). Segment sums differ from the loop by ~1 ulp (accepted; see
    ``_pl_topk_objective_loop``), so the result is allclose, not bit-identical.
    """
    ranks = np.asarray(ranks)
    gsize = np.asarray(group_sizes, dtype=np.int64)
    n = int(gsize.sum())
    n_groups = len(gsize)
    # row -> group id, and per-group start offsets for reduceat
    group_id = np.repeat(np.arange(n_groups), gsize)
    group_start = np.concatenate(([0], np.cumsum(gsize)[:-1])).astype(np.intp)

    k = len(STAGE_WEIGHTS)
    # per-group count of rank==j (j=1..k)
    counts = [
        np.bincount(group_id, weights=(ranks == j), minlength=n_groups) for j in range(1, k + 1)
    ]

    # cumulative per-group fire masks: stage j fires iff every prior stage fired AND stage-j target
    # is unique AND >=2 horses remain before stage j (remaining = group_size − (j−1)).
    fire_group: list[np.ndarray] = []
    prev = np.ones(n_groups, dtype=bool)
    for j in range(1, k + 1):
        remaining_before = gsize.astype(float) - (j - 1)  # size before removing stage-j horse
        fj = prev & (counts[j - 1] == 1) & (remaining_before >= 2)
        fire_group.append(fj)
        prev = fj

    # per-row precomputed arrays for each stage
    stages = []
    for j in range(1, k + 1):
        w = STAGE_WEIGHTS[j - 1]
        placed_before = (ranks >= 1) & (ranks <= j - 1)  # rows removed by earlier stages
        target = (ranks == j).astype(float)
        fire_row = fire_group[j - 1][group_id]           # this row's group fires stage j
        remaining_row = fire_row & ~placed_before        # rows still in play (get hess)
        stages.append(
            (w, placed_before, target, fire_row.astype(float), remaining_row.astype(float))
        )

    def fobj(preds, dataset):
        preds = np.asarray(preds, dtype=float)
        grad = np.zeros(n, dtype=float)
        hess = np.zeros(n, dtype=float)
        for w, placed_before, target, fire_row, remaining_row in stages:
            masked = np.where(placed_before, _NEG_SENTINEL, preds)
            seg_max = np.maximum.reduceat(masked, group_start)          # [n_groups]
            e = np.exp(masked - seg_max[group_id])                       # placed -> underflow 0.0
            seg_sum = np.add.reduceat(e, group_start)                    # [n_groups]
            p = e / seg_sum[group_id]                                    # softmax over remaining
            grad += w * (p - target) * fire_row                         # placed rows contribute 0
            hess += w * np.maximum(p * (1.0 - p), _HESS_FLOOR) * remaining_row
        hess = np.maximum(hess, _HESS_FLOOR)
        w_arr = dataset.get_weight()
        if w_arr is not None:
            w_arr = np.asarray(w_arr, dtype=float)
            grad *= w_arr
            hess *= w_arr
        return grad, hess

    return fobj


def winner_nll(probs, y, race_ids) -> tuple[float, int]:
    """Mean race-level −log p_winner over races with exactly one winner. Returns (nll, n).

    ``probs`` are per-horse race-normalized win probs (order matches y/race_ids). Diagnostic
    metric aligned with the objective; evaluated only on well-formed (one-winner) races.
    """
    probs = np.asarray(probs, dtype=float)
    y = np.asarray(y, dtype=float)
    race_ids = np.asarray(race_ids)
    order = np.argsort(race_ids, kind="stable")
    p_s, y_s, r_s = probs[order], y[order], race_ids[order]
    sizes = group_sizes_from_race_ids(r_s)
    nlls: list[float] = []
    start = 0
    for g in sizes:
        sl = slice(start, start + g)
        start += g
        yg = y_s[sl]
        if abs(yg.sum() - 1.0) > 1e-9:
            continue
        pw = float(p_s[sl][yg == 1][0])
        nlls.append(-np.log(max(pw, 1e-12)))
    return (float(np.mean(nlls)) if nlls else float("nan"), len(nlls))
