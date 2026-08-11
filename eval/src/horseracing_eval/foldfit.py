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


class RegimeUnsupported(RuntimeError):
    """Raised when a non-default predict regime is asked of a predictor that cannot honour it."""


def predict_over_folds_multi(
    factory: PredictorFactory,
    eval_races: list[EvalRace],
    *,
    regimes: dict[str, object],
    first_valid_year: int = FIRST_VALID_YEAR,
    num_threads: int | None = None,
    collect_raw: bool = False,
) -> tuple[dict[str, dict[str, dict[str, Prediction]]], list[EvalRace], dict]:
    """Feature 091: ONE fit per fold, predicted under SEVERAL input regimes.

    ``regimes`` maps a name to an OPAQUE predict-regime value (``None`` = the predictor's default).
    ``eval`` never inspects it — the value is built by training/CLI and handed to the predictor via
    ``set_predict_weight_mask``, so this package keeps no dependency on ``features`` (020).

    Refitting per regime would multiply the cost of an already multi-hour walk-forward for no
    reason: the fit is identical, only the inputs at predict time differ.

    ``collect_raw`` also captures the UNCALIBRATED per-race scores (``raw_win_probs``: the
    race-softmax, before isotonic). Feature 091 needs them as a mandatory diagnostic: if the
    candidate improves on raw scores but loses after calibration, the calibrator — not the
    feature — produced the result, and that is a different finding.

    Returns ``({regime: {race_id: {horse_id: Prediction}}}, valid_races, {regime: {race_id:
    {horse_id: float}}})``. The third element is empty unless ``collect_raw``.
    """
    non_default = [n for n, spec in regimes.items() if spec is not None]
    preds: dict[str, dict[str, dict[str, Prediction]]] = {name: {} for name in regimes}
    raw: dict[str, dict[str, dict[str, float]]] = {name: {} for name in regimes}
    valid_races: list[EvalRace] = []
    for fold in expanding_folds(eval_races, first_valid_year):
        predictor = factory.fit([er.context for er in fold.train], num_threads=num_threads)
        if non_default and not hasattr(predictor, "set_predict_weight_mask"):
            raise RegimeUnsupported(
                f"predictor {type(predictor).__name__} cannot switch predict regime, but "
                f"{non_default} were requested (fail-closed: a silently ignored regime would make "
                "the whole comparison meaningless while still producing numbers)"
            )
        for er in fold.valid:
            valid_races.append(er)
        for name, spec in regimes.items():
            if hasattr(predictor, "set_predict_weight_mask"):
                predictor.set_predict_weight_mask(spec)
            for er in fold.valid:
                preds[name][er.context.race_id] = predictor.predict_race(er.context)
                if collect_raw and hasattr(predictor, "raw_win_probs"):
                    ids, scores = predictor.raw_win_probs(er.context)
                    raw[name][er.context.race_id] = dict(zip(ids, map(float, scores), strict=True))
        if hasattr(predictor, "set_predict_weight_mask"):
            predictor.set_predict_weight_mask(None)  # leave the predictor as we found it
    if collect_raw and non_default and not any(raw[n] for n in regimes):
        raise RegimeUnsupported(
            "collect_raw was requested but the predictor exposes no raw_win_probs; the "
            "calibration-reversal diagnostic would be silently absent (fail-closed)"
        )
    return preds, valid_races, raw
