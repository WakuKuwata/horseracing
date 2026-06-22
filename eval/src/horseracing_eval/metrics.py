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
