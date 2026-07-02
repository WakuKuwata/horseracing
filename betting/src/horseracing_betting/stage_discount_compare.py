"""Feature 049 US2 (T015): exotic pseudo-ROI non-degradation gate for the stage discount.

Runs the exotic pseudo-ROI backtest twice on the SAME races/selection/odds — λ=1 (baseline)
vs a walk-forward λ̂ — and reports the EV-strategy pseudo-ROI difference for the includes-type
bets the discount directly touches (place / wide / trio). MUST gate (spec US2): each diff ≥ −0.005.

λ̂ is fit STRICTLY BEFORE the scored window from persisted model predictions (research D3/D4;
persisted race_predictions store the raw model p, the same distribution predict_race recomputes
in the backtest — so fit and apply share one p distribution). The two_gamma-composed betting arm
is a T021 wiring concern; the adoption gate isolates the discount's marginal ROI effect.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from horseracing_probability.model_calibration import fit_product_stage_discount
from sqlalchemy.orm import Session

from .exotic_backtest import run_exotic_backtest
from .exotic_types import BetType

#: spec US2 pre-registered non-degradation tolerance
ROI_DIFF_TOL = -0.005
_GATE_TYPES = (BetType.PLACE, BetType.WIDE, BetType.TRIO)


@dataclass
class StageDiscountRoiCompare:
    date_from: datetime.date
    date_to: datetime.date
    lambda2: float
    lambda3: float
    n_fit_races: int
    fallback: bool
    per_type: dict            # {bet_type: {base_roi, cand_roi, diff, n_bets_base, n_bets_cand}}
    must_pass: bool

    def summary(self) -> str:
        head = (
            f"stage-discount ROI compare {self.date_from}..{self.date_to}  "
            f"λ̂=({self.lambda2:.4f},{self.lambda3:.4f}) n_fit={self.n_fit_races} "
            f"fallback={self.fallback}"
        )
        lines = [head, f"  {'bet_type':<10} {'base_roi':>9} {'cand_roi':>9} {'diff':>9} "
                       f"{'n_base':>7} {'n_cand':>7}"]
        for bt, m in self.per_type.items():
            lines.append(
                f"  {bt:<10} {m['base_roi']:>9.4f} {m['cand_roi']:>9.4f} {m['diff']:>+9.4f} "
                f"{m['n_bets_base']:>7} {m['n_bets_cand']:>7}"
            )
        lines.append(f"  MUST(non-degradation, each diff ≥ {ROI_DIFF_TOL}): {self.must_pass}")
        return "\n".join(lines)


def compare_stage_discount_roi(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    min_races: int = 300,
    model_version: str | None = None,
) -> StageDiscountRoiCompare:
    # walk-forward λ̂: persisted predictions strictly before the scored window
    sd = fit_product_stage_discount(session, before_date=date_from, min_races=min_races)
    bet_types = tuple(_GATE_TYPES)
    base = run_exotic_backtest(
        session, date_from=date_from, date_to=date_to, bet_types=bet_types,
        model_version=model_version, strategies=("ev",), stage_discount=None,
    )
    cand = run_exotic_backtest(
        session, date_from=date_from, date_to=date_to, bet_types=bet_types,
        model_version=model_version, strategies=("ev",), stage_discount=sd,
    )
    per_type: dict = {}
    must = True
    for bt in bet_types:
        b = base["ev"].get(bt)
        c = cand["ev"].get(bt)
        base_roi = b.roi if b else 0.0
        cand_roi = c.roi if c else 0.0
        diff = cand_roi - base_roi
        per_type[bt] = {
            "base_roi": base_roi, "cand_roi": cand_roi, "diff": diff,
            "n_bets_base": b.n_bets if b else 0, "n_bets_cand": c.n_bets if c else 0,
        }
        if diff < ROI_DIFF_TOL:
            must = False
    return StageDiscountRoiCompare(
        date_from=date_from, date_to=date_to,
        lambda2=sd.lambda2, lambda3=sd.lambda3,
        n_fit_races=max(sd.n_races_l2, sd.n_races_l3), fallback=sd.fallback,
        per_type=per_type, must_pass=must,
    )
