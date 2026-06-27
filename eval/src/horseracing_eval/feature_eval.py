"""Feature 020 US2: walk-forward adoption gate for a new feature set.

Compares a CANDIDATE predictor (all registered features, incl. the 020 features) against a
BASELINE predictor (same pipeline with the 020 feature columns dropped) on the SAME expanding
walk-forward folds. The candidate feature set is FIXED a priori by the caller — we do NOT select
features by looking at OOS (analyze F1): evaluation model == deploy model. Adoption gate (PRIMARY):
mean win LogLoss improves AND mean win ECE does not worsen; plus fold-level guards (majority of
folds win, no fold's ECE worse beyond tol) to avoid the 035/036 false-positive (lucky-fold /
calibration regression). pseudo-ROI/Kelly are a SEPARATE diagnostic (market_edge.py), not this gate.

This module is PREDICTOR-AGNOSTIC (it never imports training): the caller passes already-constructed
``candidate``/``baseline`` predictors. eval is the lower layer (training depends on eval, not vice
versa) — injecting predictors keeps the dependency acyclic and the harness reusable.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .dataset import load_eval_races
from .harness import evaluate
from .splits import FIRST_VALID_YEAR


@dataclass(frozen=True)
class AdoptionReport:
    label: str
    n_folds: int
    mean_logloss_base: float
    mean_logloss_cand: float
    mean_brier_base: float
    mean_brier_cand: float
    mean_auc_base: float
    mean_auc_cand: float
    mean_ece_base: float
    mean_ece_cand: float
    per_fold: list[dict]          # {valid_year, logloss_base/cand/d, ece_base/cand/d}
    n_winning_folds: int          # folds where candidate LogLoss < baseline
    worst_fold_dlogloss: float    # max (cand − base) LogLoss across folds (positive = worse)
    worst_fold_dece: float        # max (cand − base) ECE across folds (positive = worse)
    primary_pass: bool            # mean LogLoss improved AND mean ECE not worse (within tol)
    adopted: bool                 # primary_pass AND fold-level guards


def evaluate_feature_adoption(
    session: Session,
    *,
    candidate,
    baseline,
    first_valid_year: int = FIRST_VALID_YEAR,
    ece_tol: float = 1e-3,
    label: str = "win",
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> AdoptionReport:
    races = load_eval_races(session, start_date=start_date, end_date=end_date)
    cres = evaluate(candidate, races, first_valid_year=first_valid_year)
    bres = evaluate(baseline, races, first_valid_year=first_valid_year)

    cby = {f["valid_year"]: f for f in cres.by_fold}
    bby = {f["valid_year"]: f for f in bres.by_fold}
    years = sorted(set(cby) & set(bby))

    per_fold: list[dict] = []
    n_win = 0
    worst_dll = float("-inf")
    worst_dece = float("-inf")
    for y in years:
        cl, bl = cby[y][label]["log_loss"], bby[y][label]["log_loss"]
        ce, be = cby[y][label]["ece"], bby[y][label]["ece"]
        dll, dece = cl - bl, ce - be
        per_fold.append({"valid_year": y, "logloss_base": bl, "logloss_cand": cl,
                         "dlogloss": dll, "ece_base": be, "ece_cand": ce, "dece": dece})
        n_win += 1 if cl < bl else 0
        worst_dll = max(worst_dll, dll)
        worst_dece = max(worst_dece, dece)

    co, bo = cres.overall[label], bres.overall[label]
    m_ll_c, m_ll_b = co["log_loss"], bo["log_loss"]
    m_ece_c, m_ece_b = co["ece"], bo["ece"]
    primary = (m_ll_c < m_ll_b) and (m_ece_c <= m_ece_b + ece_tol)
    # fold guards: majority of folds win on LogLoss AND no fold's ECE materially worse.
    fold_guard = (len(years) > 0 and n_win * 2 >= len(years) and worst_dece <= ece_tol)
    return AdoptionReport(
        label=label, n_folds=len(years),
        mean_logloss_base=m_ll_b, mean_logloss_cand=m_ll_c,
        mean_brier_base=bo["brier"], mean_brier_cand=co["brier"],
        mean_auc_base=bo["auc"], mean_auc_cand=co["auc"],
        mean_ece_base=m_ece_b, mean_ece_cand=m_ece_c,
        per_fold=per_fold, n_winning_folds=n_win,
        worst_fold_dlogloss=(worst_dll if years else 0.0),
        worst_fold_dece=(worst_dece if years else 0.0),
        primary_pass=primary, adopted=(primary and fold_guard),
    )
