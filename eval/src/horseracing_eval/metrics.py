"""Prediction-quality metrics (research R3/R4).

Standard metrics via scikit-learn; ECE is hand-rolled (label-wise, configurable
bins, per-field-size diagnostic). All operate on flat per-scoring-row arrays.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from sklearn.metrics import log_loss, ndcg_score, roc_auc_score

_CLIP = 1e-15
DEFAULT_ECE_BINS = 10


def log_loss_label(probs, labels) -> float:
    p = np.clip(np.asarray(probs, dtype=float), _CLIP, 1 - _CLIP)
    return float(log_loss(np.asarray(labels), p, labels=[0, 1]))


def brier_label(probs, labels) -> float:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    return float(np.mean((p - y) ** 2))


def auc_label(probs, labels) -> float | None:
    y = np.asarray(labels)
    if len(np.unique(y)) < 2:  # single class -> AUC undefined
        return None
    return float(roc_auc_score(y, np.asarray(probs, dtype=float)))


def ndcg_label(probs, labels, race_ids) -> float | None:
    """Mean per-race NDCG (each race is a query). Races with <2 finishers are skipped."""
    by_race: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for prob, label, rid in zip(probs, labels, race_ids, strict=True):
        by_race[rid].append((float(prob), int(label)))
    scores = []
    for rows in by_race.values():
        if len(rows) < 2:
            continue
        y_score = np.array([[r[0] for r in rows]])
        y_true = np.array([[r[1] for r in rows]])
        scores.append(ndcg_score(y_true, y_score))
    return float(np.mean(scores)) if scores else None


def ece_label(probs, labels, bins: int = DEFAULT_ECE_BINS) -> float:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    n = len(p)
    if n == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for b in range(bins):
        lo, hi = edges[b], edges[b + 1]
        mask = (p >= lo) & (p < hi) if b < bins - 1 else (p >= lo) & (p <= hi)
        cnt = int(mask.sum())
        if cnt == 0:
            continue
        conf = p[mask].mean()
        acc = y[mask].mean()
        ece += (cnt / n) * abs(conf - acc)
    return float(ece)


def ece_by_field_size(probs, labels, field_sizes, bins: int = DEFAULT_ECE_BINS) -> dict[int, float]:
    groups: dict[int, list[int]] = defaultdict(list)
    for i, fs in enumerate(field_sizes):
        groups[int(fs)].append(i)
    out: dict[int, float] = {}
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    for fs, idx in sorted(groups.items()):
        out[fs] = ece_label(p[idx], y[idx], bins=bins)
    return out


def compute_label_metrics(
    probs, labels, race_ids, field_sizes, bins: int = DEFAULT_ECE_BINS
) -> dict:
    return {
        "log_loss": log_loss_label(probs, labels),
        "brier": brier_label(probs, labels),
        "auc": auc_label(probs, labels),
        "ndcg": ndcg_label(probs, labels, race_ids),
        "ece": ece_label(probs, labels, bins=bins),
    }


# ---------------------------------------------------------------------------
# Feature 068 (evaluation contract): race-level PRIMARY + started-all + ECE variants.
# All pure functions, no DB. See specs/068-evaluation-contract-calibration/data-model.md §1.
# ---------------------------------------------------------------------------


def winner_nll(winner_probs) -> tuple[float, int]:
    """Race-level PRIMARY (FR-001): mean of ``-log(p_winner)`` over eligible races.

    ``winner_probs`` is one entry per race: the predicted win prob of the race's
    actual single winner, or ``None`` for an INELIGIBLE race (dead heat / no winner /
    unresolved / partial-ingest — spec Edge Cases). Returns ``(nll, n_excluded)`` where
    ``n_excluded`` counts the ``None`` races (surfaced, not silently dropped, D1).
    """
    eligible = [float(p) for p in winner_probs if p is not None]
    n_excluded = len(list(winner_probs)) - len(eligible)
    if not eligible:
        return float("nan"), n_excluded
    p = np.clip(np.asarray(eligible, dtype=float), _CLIP, 1 - _CLIP)
    return float(np.mean(-np.log(p))), n_excluded


def uniform_baseline_winner_nll(field_sizes) -> float:
    """Sanity-only uniform baseline (FR-007): winner NLL of the 1/N predictor.

    For a race of N started horses the uniform win prob is 1/N, so ``-log(1/N)=log(N)``.
    Computed only; NEVER a promotion comparator (T017 enforces non-use).
    """
    fs = np.asarray([f for f in field_sizes if f and f > 0], dtype=float)
    if fs.size == 0:
        return float("nan")
    return float(np.mean(np.log(fs)))


def started_all_metrics(probs, labels) -> dict:
    """Started-all diagnostic (FR-002, N1): per-horse LogLoss/Brier over the STARTED
    population (DNF/失格 carry win=0). Reuses the label metrics; the difference is the
    population fed in (started, not finished). Diagnostic-only — not a gate condition.
    """
    return {"log_loss": log_loss_label(probs, labels), "brier": brier_label(probs, labels)}


def ece_equal_mass(probs, labels, bins: int = DEFAULT_ECE_BINS) -> dict:
    """Equal-mass (quantile) ECE, tie-safe (C10): predictions are sorted and split into
    ~equal-count bins WITHOUT splitting a tied-probability plateau across a bin boundary.

    Returns ``{"ece", "n_bins", "bin_counts"}`` so the actual bin count (which can be < ``bins``
    when ties collapse boundaries) and per-bin sizes are auditable rather than hidden.
    """
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    n = len(p)
    if n == 0:
        return {"ece": 0.0, "n_bins": 0, "bin_counts": []}
    order = np.argsort(p, kind="mergesort")  # stable
    p_sorted, y_sorted = p[order], y[order]
    target = max(1, n // max(1, bins))
    ece = 0.0
    counts: list[int] = []
    start = 0
    while start < n:
        end = min(start + target, n)
        # tie-safe: extend the bin to include all rows sharing the boundary probability
        while end < n and p_sorted[end] == p_sorted[end - 1]:
            end += 1
        seg_p = p_sorted[start:end]
        seg_y = y_sorted[start:end]
        cnt = end - start
        ece += (cnt / n) * abs(seg_p.mean() - seg_y.mean())
        counts.append(cnt)
        start = end
    return {"ece": float(ece), "n_bins": len(counts), "bin_counts": counts}


def ece_by_prob_band(probs, labels, band_edges) -> dict[str, float]:
    """ECE within pre-fixed probability bands (FR-006). ``band_edges`` is a fixed,
    OOS-frozen ascending list of interior cut points (e.g. ``[0.05, 0.15, 0.30]``);
    bands are ``[0,e0), [e0,e1), ..., [ek,1]``. Empty bands are omitted.
    """
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    edges = [0.0, *list(band_edges), 1.0]
    out: dict[str, float] = {}
    for b in range(len(edges) - 1):
        lo, hi = edges[b], edges[b + 1]
        last = b == len(edges) - 2
        mask = (p >= lo) & (p <= hi) if last else (p >= lo) & (p < hi)
        cnt = int(mask.sum())
        if cnt == 0:
            continue
        key = f"[{lo:.2f},{hi:.2f}{']' if last else ')'}"
        out[key] = float(abs(p[mask].mean() - y[mask].mean()))
    return out
