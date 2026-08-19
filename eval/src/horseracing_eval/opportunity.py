"""Opportunity-set scoring — measure a feature where it actually applies.

The problem this exists for: the primary estimand is a mean over the WHOLE window, so a feature
that helps a lot in a small slice is divided by its coverage. A candidate worth −0.006 on the 25%
of races where it applies shows up as −0.0015 overall, which sits inside the measurement noise.
Twenty-plus feature attempts have landed in exactly that band. The July 2026 three-lens metric
review converged independently on this design and it has been the standing recommendation since.

The rule it implements — deliberately BOTH halves:

    superiority on the pre-registered opportunity set  AND  non-inferiority overall

Superiority alone is not adoptable. A feature that helps its slice while quietly costing the rest
of the book is worse than nothing, and the whole-window arm is what catches that.

Why the mask is INJECTED rather than computed here: ``eval`` must not import ``features`` or
``training`` (the 020 boundary), and the mask is a statement about feature availability. The caller
builds it; this module only partitions and scores. That means this module cannot verify the mask is
as-of safe — so it enforces the next best thing, which is that the mask must be DECLARED in the
frozen gate-config with an expected coverage range, and the realized coverage must land inside it.
A mask that quietly reads outcomes, or that is not the mask that was registered, almost always
moves coverage; that is what the check is for.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .bootstrap import inflate_for_seed_noise, race_day_cluster_bootstrap_ci_v1
from .dataset import population_masks


class OpportunityContractError(RuntimeError):
    """The opportunity set was not declared, or is not the set that was declared."""


@dataclass(frozen=True)
class OpportunityScores:
    definition: str
    n_races: int
    n_days: int
    n_eligible_total: int
    coverage: float
    declared_coverage: list | None
    coverage_as_declared: bool
    diff: float
    ci_low: float | None
    ci_high: float | None
    total_ci_low: float | None
    total_ci_high: float | None
    n_folds: int

    def to_dict(self) -> dict:
        return asdict(self)


def declared(cfg: dict | None) -> dict | None:
    return (cfg or {}).get("opportunity_set") or None


def score_opportunity(
    valid_races,
    cand_preds,
    act_preds,
    *,
    races: set,
    cfg: dict,
    clip_nll,
    b: int = 2000,
    seed: int = 20260818,
    alpha: float = 0.05,
    sd_fold: float = 0.0,
    k_seeds: int = 1,
) -> OpportunityScores:
    """Score the paired winner-NLL difference restricted to the pre-registered opportunity set."""
    spec = declared(cfg)
    if spec is None:
        raise OpportunityContractError(
            "an opportunity set was supplied but the gate-config declares none. Choosing the "
            "slice after seeing the numbers is the selection this design exists to prevent — "
            "declare it in the frozen config or do not pass it."
        )

    diffs_by_day: dict[str, list[float]] = {}
    n_in = 0
    n_eligible = 0
    years: set[int] = set()
    for er in valid_races:
        pop = population_masks(er)
        if not pop.eligible:
            continue
        n_eligible += 1
        if er.context.race_id not in races:
            continue
        cp = cand_preds[er.context.race_id].get(pop.winner_horse_id)
        ap = act_preds[er.context.race_id].get(pop.winner_horse_id)
        if cp is None or ap is None:
            continue
        n_in += 1
        years.add(er.context.race_date.year)
        diffs_by_day.setdefault(er.context.race_date.isoformat(), []).append(
            clip_nll(cp.win) - clip_nll(ap.win)
        )

    coverage = (n_in / n_eligible) if n_eligible else 0.0
    band = spec.get("expected_coverage")
    ok = True
    if band:
        lo, hi = float(band[0]), float(band[1])
        ok = lo <= coverage <= hi
        if not ok and spec.get("fail_closed_on_coverage", True):
            raise OpportunityContractError(
                f"realized opportunity coverage {coverage:.3f} is outside the declared range "
                f"[{lo}, {hi}]. Either the mask is not the one that was registered, or it is "
                "selecting on something it should not. Fail-closed rather than scoring it."
            )

    if not diffs_by_day:
        return OpportunityScores(
            definition=str(spec.get("definition", "")), n_races=0, n_days=0,
            n_eligible_total=n_eligible, coverage=coverage,
            declared_coverage=list(band) if band else None, coverage_as_declared=ok,
            diff=float("nan"), ci_low=None, ci_high=None,
            total_ci_low=None, total_ci_high=None, n_folds=0,
        )

    ci = race_day_cluster_bootstrap_ci_v1(diffs_by_day, b=b, seed=seed, alpha=alpha)
    total = inflate_for_seed_noise(
        ci, sd_fold=sd_fold, n_folds=max(1, len(years)), k_seeds=k_seeds, alpha=alpha
    )
    return OpportunityScores(
        definition=str(spec.get("definition", "")),
        n_races=n_in, n_days=ci.n_days, n_eligible_total=n_eligible, coverage=coverage,
        declared_coverage=list(band) if band else None, coverage_as_declared=ok,
        diff=ci.point, ci_low=ci.ci_low, ci_high=ci.ci_high,
        total_ci_low=total.ci_low, total_ci_high=total.ci_high, n_folds=len(years),
    )
