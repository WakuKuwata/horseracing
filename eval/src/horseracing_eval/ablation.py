"""Feature 020 US2: group ablation (diagnostic, NOT a feature-selection gate).

For each 020 feature group, drop ONLY that group and measure the walk-forward OOS win LogLoss delta
vs the full candidate. contribution = (logloss_without_group − logloss_full): positive means
removing the group HURTS (i.e. the group helps). Attributes which group drives a change (recent_form
and human_form share race history, so per-group ablation separates them). The candidate feature set
stays fixed a priori — ablation does NOT pick adopted features.

PREDICTOR-AGNOSTIC: the caller passes ``make_predictor(drop_features)`` (a factory) and ``groups``
(group name → its feature columns). eval never imports training (training depends on eval).
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .dataset import load_eval_races
from .harness import evaluate
from .splits import FIRST_VALID_YEAR


@dataclass(frozen=True)
class AblationReport:
    label: str
    full_logloss: float
    group_contribution: dict[str, float]  # group → (logloss_without − full); positive = group helps


def evaluate_group_ablation(
    session: Session,
    *,
    make_predictor: Callable[[tuple[str, ...]], object],
    groups: Mapping[str, Sequence[str]],
    first_valid_year: int = FIRST_VALID_YEAR,
    label: str = "win",
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> AblationReport:
    races = load_eval_races(session, start_date=start_date, end_date=end_date)

    def _logloss(drop: tuple[str, ...]) -> float:
        pred = make_predictor(drop)
        return evaluate(pred, races, first_valid_year=first_valid_year).overall[label]["log_loss"]

    full = _logloss(())
    contribution: dict[str, float] = {}
    for grp in sorted(groups):
        cols = tuple(groups[grp])
        contribution[grp] = _logloss(cols) - full  # positive = dropping the group worsens LogLoss
    return AblationReport(label=label, full_logloss=full, group_contribution=contribution)
