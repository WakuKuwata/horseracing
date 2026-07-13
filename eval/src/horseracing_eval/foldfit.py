"""Per-outer-fold re-fit harness + injection Protocol (Feature 068, FR-003, codex C1).

The saved model artifact is a FULL-HISTORY serving model; applying it to past races is
in-sample (leaky). So paired-eval never evaluates a stored booster — instead each arm is
described by a ``PredictorFactory`` that RE-FITS a predictor on the outer-train rows of each
fold. ``eval`` defines only the Protocol; ``training`` builds the concrete factory from a
``ModelRecipe`` and injects it, so ``eval`` never imports ``training`` (020 boundary).

Determinism: the factory is expected to honour ``num_threads`` (SC-002); the harness passes it
through so the SC-002 verification run can pin ``num_threads=1`` while heavy A–D screening may
run multi-thread (research I1/U1).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .dataset import EvalRace
from .predictor import Prediction, Predictor, RaceContext
from .splits import FIRST_VALID_YEAR, expanding_folds


@runtime_checkable
class PredictorFactory(Protocol):
    """Injected by the CLI (training side) — builds a freshly-fit predictor per fold.

    Implementations MUST reject ``market_offset=true`` recipes fail-closed (FR-019) before
    reaching here; the harness treats the factory as opaque.
    """

    #: plain-dict audit view of the recipe (no training types cross the boundary)
    recipe_meta: dict
    #: deterministic hash of the recipe
    recipe_hash: str

    def fit(self, train_races: list[RaceContext], *, num_threads: int | None = None) -> Predictor:
        """Fit a predictor on ALL outer-train rows of one fold and return it."""
        ...


def predict_over_folds(
    factory: PredictorFactory,
    eval_races: list[EvalRace],
    *,
    first_valid_year: int = FIRST_VALID_YEAR,
    num_threads: int | None = None,
) -> tuple[dict[str, dict[str, Prediction]], list[EvalRace]]:
    """Re-fit ``factory`` on each expanding fold's train rows and predict its valid races.

    Returns ``(predictions_by_race_id, valid_races_in_order)``. The saved booster is never
    used — each fold is a fresh fit on outer-train (codex C1). Folds are deterministic given
    ``eval_races`` so both paired arms see the identical valid set (model-blind, FR-003).
    """
    preds: dict[str, dict[str, Prediction]] = {}
    valid_races: list[EvalRace] = []
    for fold in expanding_folds(eval_races, first_valid_year):
        predictor = factory.fit([er.context for er in fold.train], num_threads=num_threads)
        for er in fold.valid:
            preds[er.context.race_id] = predictor.predict_race(er.context)
            valid_races.append(er)
    return preds, valid_races
