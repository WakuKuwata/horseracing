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


def pl_topk_objective(group_sizes: list[int], ranks):
    """Feature 042: Plackett-Luce top-k (k=len(STAGE_WEIGHTS)) sequential objective.

    ``ranks``: per-row finishing rank (1..k) or 0 (others/DNF), aligned to the sorted rows.
    Stage j softmaxes over the not-yet-placed horses and targets the j-th finisher:
    grad += w_j(p − y_j), hess += w_j·p(1−p) on the remaining set. Stage 1 without a
    unique winner neutralizes the whole group (as cond_logit); a later stage without a
    unique target (dead-heat / missing) or with <2 remaining horses breaks — earlier
    stages' gradients are kept. Sample weight is applied explicitly (LightGBM does not
    auto-apply it to custom objectives); no weight -> unchanged.
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
