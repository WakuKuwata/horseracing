"""Expanding-window walk-forward evaluation harness (deterministic)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .consistency import DEFAULT_TOLERANCE, check_consistency
from .dataset import EvalRace
from .metrics import DEFAULT_ECE_BINS, compute_label_metrics, ece_by_field_size
from .predictor import Predictor
from .splits import FIRST_VALID_YEAR, expanding_folds

_LABELS = ("win", "top2", "top3")


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

    def to_summary(self) -> dict:
        """data-model.md metrics_summary jsonb 形。"""
        return {
            "eval": {
                "scheme": self.scheme,
                "valid_years": self.valid_years,
                "tolerance": self.tolerance,
                "ece_bins": self.ece_bins,
                "overall": self.overall,
                "by_fold": self.by_fold,
                "by_field_size_ece": self.by_field_size_ece,
            }
        }


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
) -> EvalResult:
    tol = tolerance or DEFAULT_TOLERANCE
    overall = {label: _Rows() for label in _LABELS}
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

    by_field = {
        label: ece_by_field_size(r.probs, r.labels, r.field_sizes, bins=ece_bins)
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
    )
