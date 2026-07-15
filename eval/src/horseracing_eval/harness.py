"""Expanding-window walk-forward evaluation harness (deterministic)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .consistency import DEFAULT_TOLERANCE, check_consistency
from .dataset import EvalRace, population_masks
from .metrics import DEFAULT_ECE_BINS, compute_label_metrics, ece_by_field_size
from .predictor import Predictor
from .splits import FIRST_VALID_YEAR, expanding_folds

_LABELS = ("win", "top2", "top3")

#: Feature 021 US2: a reliability bin with fewer than this many samples is suppressed (not plotted)
#: because its realized rate / CI is too unstable to display honestly (codex R5).
RELIABILITY_MIN_COUNT = 30
_WILSON_Z = 1.96  # 95% interval


def _wilson(k: int, n: int) -> tuple[float, float] | tuple[None, None]:
    """95% Wilson interval for a binomial proportion k/n (count-aware uncertainty, FR-006b)."""
    if n <= 0:
        return None, None
    p = k / n
    z2 = _WILSON_Z * _WILSON_Z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (_WILSON_Z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, center - half), min(1.0, center + half)


def reliability_bins(
    probs: list[float], labels: list[int], *,
    n_bins: int = 10, min_count: int = RELIABILITY_MIN_COUNT,
) -> list[dict]:
    """Equal-width reliability bins on [0,1]: pred_mean vs realized_rate + Wilson CI + count.

    Bins with < ``min_count`` samples are marked ``suppressed`` (R5). Empty bins are skipped.
    """
    out: list[dict] = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        last = b == n_bins - 1
        idx = [i for i in range(len(probs)) if (lo <= probs[i] < hi) or (last and probs[i] == 1.0)]
        if not idx:
            continue
        count = len(idx)
        wins = sum(labels[i] for i in idx)
        ci_lo, ci_hi = _wilson(wins, count)
        out.append({
            "pred_lo": lo, "pred_hi": hi,
            "pred_mean": sum(probs[i] for i in idx) / count,
            "realized_rate": wins / count,
            "realized_ci_low": ci_lo, "realized_ci_high": ci_hi,
            "count": count, "suppressed": count < min_count,
        })
    return out


@dataclass
class _Rows:
    probs: list[float] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)
    race_ids: list[str] = field(default_factory=list)
    field_sizes: list[int] = field(default_factory=list)


@dataclass
class EvalResult:
    scheme: str
    valid_years: list[int]
    tolerance: dict[str, float]
    ece_bins: int
    overall: dict[str, dict]
    by_fold: list[dict]
    by_field_size_ece: dict[str, dict[int, float]]
    #: Feature 021 US2: walk-forward OOS reliability per label {label: {bins, n_total}} (read by the
    #: API calibration endpoint via metrics_summary). OOS by construction (pooled valid folds).
    reliability: dict[str, dict] = field(default_factory=dict)
    #: Feature 073 US1 (FR-003): started-all WIN metrics (DNF=0) when evaluate(started_all=True).
    #: None (key omitted from the summary) otherwise, so existing summaries stay byte-identical.
    started_all_win: dict | None = None

    def to_summary(self) -> dict:
        """data-model.md metrics_summary jsonb 形。"""
        eval_block = {
            "scheme": self.scheme,
            "valid_years": self.valid_years,
            "tolerance": self.tolerance,
            "ece_bins": self.ece_bins,
            "overall": self.overall,
            "by_fold": self.by_fold,
            "by_field_size_ece": self.by_field_size_ece,
            "reliability": self.reliability,
        }
        if self.started_all_win is not None:  # Feature 073: additive, absent by default
            eval_block["started_all_win"] = self.started_all_win
        return {"eval": eval_block}


def _score_race(rows_by_label: dict[str, _Rows], er: EvalRace, preds) -> None:
    field_size = len(er.context.started_horses)
    for sl in er.labels:  # finished horses only
        pred = preds.get(sl.horse_id)
        if pred is None:
            continue
        for label in _LABELS:
            r = rows_by_label[label]
            r.probs.append(float(getattr(pred, label)))
            r.labels.append(int(getattr(sl, label)))
            r.race_ids.append(er.context.race_id)
            r.field_sizes.append(field_size)


def _score_race_started_all(rows: _Rows, er: EvalRace, preds) -> None:
    """Feature 073 US1 (FR-003): score WIN over ALL started horses (DNF/DSQ label = 0), matching
    the paired-eval started-all population so the harness body and the adoption path agree on the
    scored population (training learns on started-all; finished-only scoring was a mismatch)."""
    pop = population_masks(er)
    for hid in pop.started_horse_ids:
        pred = preds.get(hid)
        if pred is None:
            continue
        rows.probs.append(float(pred.win))
        rows.labels.append(int(pop.started_win[hid]))
        rows.race_ids.append(er.context.race_id)
        rows.field_sizes.append(pop.field_size)


def _metrics_for(rows_by_label: dict[str, _Rows], bins: int) -> dict[str, dict]:
    return {
        label: compute_label_metrics(
            r.probs, r.labels, r.race_ids, r.field_sizes, bins=bins
        )
        for label, r in rows_by_label.items()
        if r.probs
    }


def evaluate(
    predictor: Predictor,
    eval_races: list[EvalRace],
    *,
    first_valid_year: int = FIRST_VALID_YEAR,
    ece_bins: int = DEFAULT_ECE_BINS,
    tolerance: dict[str, float] | None = None,
    started_all: bool = False,
) -> EvalResult:
    tol = tolerance or DEFAULT_TOLERANCE
    overall = {label: _Rows() for label in _LABELS}
    # Feature 073 US1 (FR-003): opt-in started-all WIN accumulator (default off => byte-identical).
    started_all_rows = _Rows()
    by_fold: list[dict] = []
    valid_years: list[int] = []

    for fold in expanding_folds(eval_races, first_valid_year):
        predictor.fit([er.context for er in fold.train])  # baselines: no-op
        fold_rows = {label: _Rows() for label in _LABELS}
        n_races = 0
        for er in fold.valid:
            preds = predictor.predict_race(er.context)
            check_consistency(preds, tol)  # over started horses, fail-fast
            _score_race(fold_rows, er, preds)
            if started_all:
                _score_race_started_all(started_all_rows, er, preds)
            n_races += 1
        valid_years.append(fold.valid_year)
        by_fold.append(
            {"valid_year": fold.valid_year, "n_races": n_races, **_metrics_for(fold_rows, ece_bins)}
        )
        for label in _LABELS:
            overall[label].probs.extend(fold_rows[label].probs)
            overall[label].labels.extend(fold_rows[label].labels)
            overall[label].race_ids.extend(fold_rows[label].race_ids)
            overall[label].field_sizes.extend(fold_rows[label].field_sizes)

    started_all_win = None
    if started_all and started_all_rows.probs:
        started_all_win = compute_label_metrics(
            started_all_rows.probs, started_all_rows.labels,
            started_all_rows.race_ids, started_all_rows.field_sizes, bins=ece_bins,
        )
        started_all_win["n_started"] = len(started_all_rows.probs)

    by_field = {
        label: ece_by_field_size(r.probs, r.labels, r.field_sizes, bins=ece_bins)
        for label, r in overall.items()
        if r.probs
    }
    reliability = {
        label: {"bins": reliability_bins(r.probs, r.labels), "n_total": len(r.probs)}
        for label, r in overall.items()
        if r.probs
    }
    return EvalResult(
        scheme="expanding_yearly",
        valid_years=valid_years,
        tolerance=tol,
        ece_bins=ece_bins,
        overall=_metrics_for(overall, ece_bins),
        by_fold=by_fold,
        by_field_size_ece=by_field,
        reliability=reliability,
        started_all_win=started_all_win,
    )
