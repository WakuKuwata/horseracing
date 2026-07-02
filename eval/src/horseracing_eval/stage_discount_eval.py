"""Feature 049 US2: pre-registered A/B for the stage discount (research D3/D6).

Single training pass per fold: the predictor produces win probabilities; baseline
(λ=1, = current production derivation) and candidate (λ̂ fit from PRIOR folds' OOS
predictions) derive top2/top3 from the SAME win vector, so win metrics are identical
by construction and the difference is the pure effect of the derivation layer.

The gate is fixed in spec US2 and MUST NOT be adjusted after seeing numbers (憲法 III).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .baselines import harville_topk
from .dataset import EvalRace
from .metrics import ece_label, log_loss_label
from .predictor import Predictor
from .splits import FIRST_VALID_YEAR, expanding_folds
from .stage_discount import (
    DEFAULT_MIN_RACES,
    IDENTITY,
    StageDiscount,
    TopkSample,
    fit_stage_discount,
)

#: worst single-fold top3 LogLoss regression tolerated (spec US2 guard, 020/023 同型)
WORST_FOLD_TOP3_DLOGLOSS_TOL = 5e-3


def decide_gate(baseline, candidate, winning_folds_top3, worst_fold_top3_dloss, n_folds):
    """Pure pre-registered gate (spec US2). Returns (primary_pass, guard_pass).

    PRIMARY: top2 AND top3 LogLoss improve AND top2 AND top3 ECE improve AND strict
    majority of folds improve top3 LogLoss. GUARD: worst-fold top3 LogLoss regression
    within tolerance. (win identity is checked separately and folded into `adopted`.)
    """
    primary = (
        candidate["top2"]["log_loss"] < baseline["top2"]["log_loss"]
        and candidate["top3"]["log_loss"] < baseline["top3"]["log_loss"]
        and candidate["top2"]["ece"] < baseline["top2"]["ece"]
        and candidate["top3"]["ece"] < baseline["top3"]["ece"]
        and winning_folds_top3 * 2 > n_folds
    )
    guard = worst_fold_top3_dloss <= WORST_FOLD_TOP3_DLOGLOSS_TOL
    return primary, guard


def _finishers_from_labels(labels) -> tuple[str | None, str | None, str | None]:
    """Reconstruct unique 1st/2nd/3rd from <=k indicator labels. A dead heat at any
    position yields >1 horse at that delta -> None (excluded from that stage)."""
    ones = [sl.horse_id for sl in labels if sl.win == 1]
    twos = [sl.horse_id for sl in labels if sl.top2 == 1 and sl.win == 0]
    threes = [sl.horse_id for sl in labels if sl.top3 == 1 and sl.top2 == 0]
    return (
        ones[0] if len(ones) == 1 else None,
        twos[0] if len(twos) == 1 else None,
        threes[0] if len(threes) == 1 else None,
    )


@dataclass
class _Rows:
    probs: list[float] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)


def _metrics(rows: _Rows, bins: int) -> dict:
    return {
        "log_loss": log_loss_label(rows.probs, rows.labels),
        "ece": ece_label(rows.probs, rows.labels, bins),
        "n": len(rows.probs),
    }


@dataclass
class StageDiscountReport:
    n_folds: int
    fold_lambdas: list[dict]        # [{valid_year, lambda2, lambda3, n_fit, fallback}]
    baseline: dict                  # {win, top2, top3} overall metrics (λ=1)
    candidate: dict                 # {win, top2, top3} overall metrics (λ̂)
    by_fold: list[dict]             # per fold {valid_year, base_top3_ll, cand_top3_ll, ...}
    win_max_abs_diff: float         # must be 0.0 (INV-S2 across the whole run)
    winning_folds_top3: int         # folds where candidate top3 LogLoss < baseline
    worst_fold_top3_dloss: float    # max (cand - base) top3 LogLoss over folds
    primary_pass: bool
    guard_pass: bool
    win_identical: bool
    adopted: bool

    def summary(self) -> str:
        b, c = self.baseline, self.candidate
        lines = [
            f"folds={self.n_folds} adopted={self.adopted}",
            f"  win      LL base={b['win']['log_loss']:.5f} cand={c['win']['log_loss']:.5f}"
            f"  (max|Δwin|={self.win_max_abs_diff:.2e}, identical={self.win_identical})",
            f"  top2  LL {b['top2']['log_loss']:.5f} -> {c['top2']['log_loss']:.5f}"
            f"   ECE {b['top2']['ece']:.5f} -> {c['top2']['ece']:.5f}",
            f"  top3  LL {b['top3']['log_loss']:.5f} -> {c['top3']['log_loss']:.5f}"
            f"   ECE {b['top3']['ece']:.5f} -> {c['top3']['ece']:.5f}",
            f"  winning_folds(top3 LL)={self.winning_folds_top3}/{self.n_folds}"
            f"  worst_fold_top3_dLL={self.worst_fold_top3_dloss:+.5f}",
            f"  primary={self.primary_pass} guard={self.guard_pass}",
        ]
        return "\n".join(lines)


def evaluate_stage_discount(
    predictor: Predictor,
    eval_races: list[EvalRace],
    *,
    first_valid_year: int = FIRST_VALID_YEAR,
    min_races: int = DEFAULT_MIN_RACES,
    ece_bins: int = 10,
) -> StageDiscountReport:
    labels_all = ("win", "top2", "top3")
    base = {lb: _Rows() for lb in labels_all}
    cand = {lb: _Rows() for lb in labels_all}
    prior_samples: list[TopkSample] = []
    fold_lambdas: list[dict] = []
    by_fold: list[dict] = []
    win_max_abs_diff = 0.0

    for fold in expanding_folds(eval_races, first_valid_year):
        predictor.fit([er.context for er in fold.train])
        sd = fit_stage_discount(prior_samples, min_races=min_races) if prior_samples else IDENTITY
        fold_lambdas.append({
            "valid_year": fold.valid_year, "lambda2": sd.lambda2, "lambda3": sd.lambda3,
            "n_fit": len(prior_samples), "fallback": sd.fallback,
        })
        fold_base = {lb: _Rows() for lb in labels_all}
        fold_cand = {lb: _Rows() for lb in labels_all}
        new_samples: list[TopkSample] = []

        for er in fold.valid:
            preds = predictor.predict_race(er.context)
            ids = sorted(preds)
            pos = {h: k for k, h in enumerate(ids)}
            win_list = [preds[h].win for h in ids]
            # baseline = production derivation (λ=1); candidate = same win, discounted tail
            base2 = [preds[h].top2 for h in ids]
            base3 = [preds[h].top3 for h in ids]
            if sd.is_identity:
                c2, c3 = base2, base3
            else:
                c2, c3 = harville_topk(win_list, lambda2=sd.lambda2, lambda3=sd.lambda3)

            for sl in er.labels:  # finished horses only
                if sl.horse_id not in pos:
                    continue
                k = pos[sl.horse_id]
                for lb, val in (("win", win_list[k]),):
                    fold_base[lb].probs.append(val); fold_base[lb].labels.append(int(getattr(sl, lb)))
                    fold_cand[lb].probs.append(val); fold_cand[lb].labels.append(int(getattr(sl, lb)))
                fold_base["top2"].probs.append(base2[k]); fold_base["top2"].labels.append(sl.top2)
                fold_base["top3"].probs.append(base3[k]); fold_base["top3"].labels.append(sl.top3)
                fold_cand["top2"].probs.append(c2[k]); fold_cand["top2"].labels.append(sl.top2)
                fold_cand["top3"].probs.append(c3[k]); fold_cand["top3"].labels.append(sl.top3)

            i1, i2, i3 = _finishers_from_labels(er.labels)
            if i1 is not None and i1 in pos:
                new_samples.append(TopkSample(
                    win=tuple(win_list),
                    i1=pos[i1],
                    i2=pos[i2] if (i2 is not None and i2 in pos) else None,
                    i3=pos[i3] if (i3 is not None and i3 in pos) else None,
                ))

        base_top3_ll = log_loss_label(fold_base["top3"].probs, fold_base["top3"].labels)
        cand_top3_ll = log_loss_label(fold_cand["top3"].probs, fold_cand["top3"].labels)
        by_fold.append({
            "valid_year": fold.valid_year,
            "base_top3_ll": base_top3_ll, "cand_top3_ll": cand_top3_ll,
            "base_top2_ll": log_loss_label(fold_base["top2"].probs, fold_base["top2"].labels),
            "cand_top2_ll": log_loss_label(fold_cand["top2"].probs, fold_cand["top2"].labels),
        })
        # win probs must be identical across baseline/candidate (INV-S2)
        for a, b in zip(fold_base["win"].probs, fold_cand["win"].probs, strict=True):
            win_max_abs_diff = max(win_max_abs_diff, abs(a - b))
        for lb in labels_all:
            base[lb].probs.extend(fold_base[lb].probs); base[lb].labels.extend(fold_base[lb].labels)
            cand[lb].probs.extend(fold_cand[lb].probs); cand[lb].labels.extend(fold_cand[lb].labels)
        prior_samples.extend(new_samples)

    baseline = {lb: _metrics(base[lb], ece_bins) for lb in labels_all}
    candidate = {lb: _metrics(cand[lb], ece_bins) for lb in labels_all}

    winning = sum(1 for f in by_fold if f["cand_top3_ll"] < f["base_top3_ll"])
    worst = max((f["cand_top3_ll"] - f["base_top3_ll"]) for f in by_fold) if by_fold else 0.0
    n_folds = len(by_fold)
    win_identical = win_max_abs_diff == 0.0
    primary, guard = decide_gate(baseline, candidate, winning, worst, n_folds)

    return StageDiscountReport(
        n_folds=n_folds,
        fold_lambdas=fold_lambdas,
        baseline=baseline,
        candidate=candidate,
        by_fold=by_fold,
        win_max_abs_diff=win_max_abs_diff,
        winning_folds_top3=winning,
        worst_fold_top3_dloss=worst,
        primary_pass=primary,
        guard_pass=guard,
        win_identical=win_identical,
        adopted=primary and guard and win_identical,
    )
