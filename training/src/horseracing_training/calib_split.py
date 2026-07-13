"""Feature 068 US2 arms C/D: full-history booster + OOF power calibrator (research D3/C6/C7).

A (70/30) and B (90/10) carve a train-internal calibration holdout, so the booster never learns
the latest rows. C/D instead fit the booster on the FULL train window and fit the calibrator on
strict-past out-of-fold (OOF) predictions, returning the booster's own learning to the latest
period. The calibrator is a race-normalized power ``p'∝p^γ`` (048 canonical, acts on the Σ=1
vector, IV). For a softmax objective, temperature-on-logits and power-on-p are the same family
(T020), so C (temperature) and D (power) collapse to this single OOF power calibrator; the
distinction only matters for a raw-score temperature under a non-softmax objective (not used).

OOF is EXPANDING STRICT-PAST by race-day (FR-014a, codex C6): the train window is split into day
blocks, and block k is predicted by a booster trained only on strictly-earlier day blocks, so
``max(train_day) < prediction_day`` for every OOF row. The feature matrix is built once and shared
across the full-history booster and every inner OOF booster (the build dominates runtime).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceResult
from horseracing_eval.hashing import stable_hash
from horseracing_eval.predictor import Predictor, RaceContext
from horseracing_probability.model_calibration import _apply_gamma, fit_power_gamma
from sqlalchemy import select
from sqlalchemy.orm import Session

from .dataset import TrainingMatrix
from .predictor import LightGBMPredictor, assemble_predictions
from .recipe import ModelRecipe


def _single_winners(session: Session, race_ids) -> dict[str, str]:
    """race_id -> winner horse_id, only for races with EXACTLY one finish_order==1 (dead heats
    dropped — a winner-NLL calibration sample needs an unambiguous winner)."""
    ids = list(race_ids)
    stmt = (
        select(RaceResult.race_id, RaceResult.horse_id)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
        .where(RaceResult.finish_order == 1)
        .where(RaceResult.race_id.in_(ids))
    )
    winners: dict[str, str] = {}
    counts: dict[str, int] = {}
    for rid, hid in session.execute(stmt):
        counts[rid] = counts.get(rid, 0) + 1
        winners[rid] = hid
    return {rid: h for rid, h in winners.items() if counts[rid] == 1}


def day_block_partition(days: list, n_oof: int):
    """Yield ``(earlier_days, block_days)`` for expanding strict-past OOF (FR-014a, codex C6).

    ``days`` is the sorted list of distinct race-days. Days are cut into ``n_oof`` contiguous
    groups; block k (k>=1) pairs with ALL strictly-earlier days, guaranteeing
    ``max(earlier_days) < min(block_days)`` — no OOF row is scored by a same-or-later day.
    """
    cuts = [int(round(len(days) * k / n_oof)) for k in range(n_oof + 1)]
    for k in range(1, n_oof):
        earlier = days[: cuts[k]]
        block = days[cuts[k]: cuts[k + 1]]
        if earlier and block:
            yield earlier, block


class OofCalibratedPredictor:
    """Full-history booster + strict-past OOF power calibrator (C/D)."""

    is_leaky_reference = False

    def __init__(
        self,
        session: Session,
        recipe: ModelRecipe,
        *,
        shared_data: TrainingMatrix | None = None,
        n_oof_blocks: int = 3,
    ) -> None:
        self.session = session
        self.recipe = recipe
        self._shared = shared_data
        self.n_oof = n_oof_blocks
        self._base: LightGBMPredictor | None = None
        self.gamma_ = 1.0
        self.n_oof_samples_ = 0

    def _make_base(self) -> LightGBMPredictor:
        p = LightGBMPredictor(
            self.session,
            objective=self.recipe.objective,
            calibration="none",  # booster gives race-normalized p; OOF power is applied on top
            target_encode_cols=self.recipe.target_encode_cols,
            te_smoothing=self.recipe.te_smoothing,
            seed=self.recipe.seed,
        )
        if self._shared is not None:
            p._data = self._shared
        return p

    def fit(self, train_races: list[RaceContext], *, num_threads: int | None = None):
        self._base = self._make_base()
        self._base.fit(train_races)
        samples = self._oof_samples(train_races)
        if samples:
            self.gamma_, self.n_oof_samples_ = fit_power_gamma(samples)
        return self

    def _oof_samples(self, train_races: list[RaceContext]):
        """Expanding strict-past OOF by race-day (each block predicted by strictly-earlier days)."""
        races = sorted(train_races, key=lambda r: (r.race_date, r.race_id))
        days = sorted({r.race_date for r in races})
        if len(days) < self.n_oof * 2:
            return []
        winners = _single_winners(self.session, [r.race_id for r in races])
        samples = []
        for earlier_days, block_days in day_block_partition(days, self.n_oof):
            eset, bset = set(earlier_days), set(block_days)
            earlier = [r for r in races if r.race_date in eset]
            block = [r for r in races if r.race_date in bset]
            if not earlier or not block:
                continue
            pred = self._make_base()
            pred.fit(earlier)
            for ctx in block:
                w = winners.get(ctx.race_id)
                if w is None:
                    continue
                pr = pred.predict_race(ctx)
                p = {hid: v.win for hid, v in pr.items()}
                if w in p and len(p) >= 2:
                    samples.append((p, w))
        return samples

    def predict_race(self, race: RaceContext) -> dict:
        assert self._base is not None
        base = self._base.predict_race(race)
        p = {hid: v.win for hid, v in base.items()}
        pcal = _apply_gamma(p, self.gamma_)  # race-normalized power, Σ=1 preserved (IV)
        started_ids = [h.horse_id for h in race.started_horses]
        win_scores = np.asarray([pcal[hid] for hid in started_ids], dtype=float)
        return assemble_predictions(started_ids, win_scores)


@dataclass
class CalibSplitFactory:
    """eval PredictorFactory for the C/D OOF-power arm (recipe-refit per outer fold)."""

    session: Session
    recipe: ModelRecipe
    n_oof_blocks: int = 3
    _pred: OofCalibratedPredictor | None = field(default=None, init=False, repr=False)
    _shared: TrainingMatrix | None = field(default=None, init=False, repr=False)

    @property
    def recipe_meta(self) -> dict:
        return {**self.recipe.meta(), "arm": "oof_power", "n_oof_blocks": self.n_oof_blocks}

    @property
    def recipe_hash(self) -> str:
        return stable_hash(self.recipe_meta)

    def fit(self, train_races: list[RaceContext], *, num_threads: int | None = None) -> Predictor:
        if self._shared is None:
            tmp = LightGBMPredictor(
                self.session, objective=self.recipe.objective, calibration="none"
            )
            self._shared = tmp._ensure_data()
        if self._pred is None:
            self._pred = OofCalibratedPredictor(
                self.session, self.recipe,
                shared_data=self._shared, n_oof_blocks=self.n_oof_blocks,
            )
        self._pred.fit(train_races)
        return self._pred
