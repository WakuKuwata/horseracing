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

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceResult
from horseracing_eval.hashing import stable_hash
from horseracing_eval.predictor import Predictor, RaceContext
from horseracing_probability.model_calibration import _apply_gamma, fit_power_gamma
from sqlalchemy import select
from sqlalchemy.orm import Session

from .calibration import DEFAULT_CLIP, Calibrator, fit_calibrator
from .dataset import TrainingMatrix
from .predictor import LightGBMPredictor, assemble_predictions
from .recipe import ModelRecipe

#: psycopg3 caps bound parameters at 65,535 per statement; wide-window folds train on 60k+
#: races, so the winner lookup must chunk its IN list (results are merged, order-independent).
_IN_CHUNK = 20_000

#: Feature 085 (arm E) pre-registered sufficiency floors for the OOF isotonic sample, fixed
#: BEFORE any OOS run (constitution III). A fold below ANY floor does not get an isotonic
#: calibrator: it falls back to identity and records why, and a strict (confirmatory) run
#: refuses outright rather than silently scoring "full-history booster + identity" as arm E.
MIN_OOF_RACES = 200
MIN_OOF_ROWS = 2_000
MIN_OOF_POSITIVES = 200
MIN_OOF_DISTINCT_SCORES = 2


class ArmNotServable(RuntimeError):
    """arm E cannot be shipped as-is; refuse rather than register a misdescribed model."""


class InsufficientOofSample(RuntimeError):
    """arm E could not fit its OOF calibrator and strict mode forbids the identity fallback."""


def _started_all_outcomes(session: Session, race_ids) -> dict[str, tuple[int, set[str]]]:
    """race_id -> (n_result_rows, {horse_id of every FINISHED 1st place}).

    Feature 085 (arm E) label source. Deliberately NOT ``_single_winners``:

    - **dead heats are kept**, with every finished 1st-place horse labelled 1 (a per-horse
      isotonic sample has no reason to drop the race, unlike a winner-NLL sample which needs an
      unambiguous winner);
    - ``n_result_rows`` counts result rows of ANY status, so the caller can apply the evaluator's
      fail-closed partial-ingest rule (``eval.dataset.population_masks``: fewer result rows than
      started horses means some started horse has no outcome at all, so "absent = 0" is
      unverifiable and the race must be excluded rather than labelled all-zero).
    """
    ids = list(race_ids)
    counts: dict[str, int] = {}
    winners: dict[str, set[str]] = {}
    for i in range(0, len(ids), _IN_CHUNK):
        chunk = ids[i : i + _IN_CHUNK]
        stmt = (
            select(
                RaceResult.race_id, RaceResult.horse_id,
                RaceResult.result_status, RaceResult.finish_order,
            )
            .where(RaceResult.race_id.in_(chunk))
        )
        for rid, hid, status, order in session.execute(stmt):
            counts[rid] = counts.get(rid, 0) + 1
            if status == ResultStatus.FINISHED and order == 1:
                winners.setdefault(rid, set()).add(hid)
    return {rid: (n, winners.get(rid, set())) for rid, n in counts.items()}


def _single_winners(session: Session, race_ids) -> dict[str, str]:
    """race_id -> winner horse_id, only for races with EXACTLY one finish_order==1 (dead heats
    dropped — a winner-NLL calibration sample needs an unambiguous winner)."""
    ids = list(race_ids)
    winners: dict[str, str] = {}
    counts: dict[str, int] = {}
    for i in range(0, len(ids), _IN_CHUNK):
        stmt = (
            select(RaceResult.race_id, RaceResult.horse_id)
            .where(RaceResult.result_status == ResultStatus.FINISHED)
            .where(RaceResult.finish_order == 1)
            .where(RaceResult.race_id.in_(ids[i : i + _IN_CHUNK]))
        )
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


#: calibrator families this predictor can fit on strict-past OOF predictions.
#: ``power`` = 068 arm C/D (single γ, race-normalized, Σ=1 preserving).
#: ``isotonic`` = 085 arm E (non-parametric per-horse map on the raw race-softmax, Σ=1 restored
#: by ``assemble_predictions`` — exactly A's deployed semantics).
OOF_METHODS: tuple[str, ...] = ("power", "isotonic")


class OofCalibratedPredictor:
    """Full-history booster + strict-past OOF calibrator (068 arm C/D, 085 arm E)."""

    is_leaky_reference = False

    def __init__(
        self,
        session: Session,
        recipe: ModelRecipe,
        *,
        shared_data: TrainingMatrix | None = None,
        n_oof_blocks: int = 3,
        method: str = "power",
        require_sufficient: bool = True,
    ) -> None:
        if method not in OOF_METHODS:
            raise ValueError(f"unknown OOF calibrator method: {method!r} (expected {OOF_METHODS})")
        self.session = session
        self.recipe = recipe
        self._shared = shared_data
        self.n_oof = n_oof_blocks
        self.method = method
        # Feature 085 (§3.3): in a pre-registered run an insufficient fold must NOT be scored as
        # arm E with an identity calibrator. False = fall back to identity and record the reason
        # (used by tests and by exploratory runs).
        self.require_sufficient = require_sufficient
        self._base: LightGBMPredictor | None = None
        self._reset_calibration_state()

    def _reset_calibration_state(self) -> None:
        """Clear every learned/provenance field. Called at the start of each fit: the factory
        reuses one predictor across outer folds, so a previous fold's calibrator must never
        survive into a fold that fits nothing."""
        self.gamma_ = 1.0
        self.calibrator_: Calibrator | None = None
        self.n_oof_samples_ = 0
        self.oof_info_: dict = {
            "method": self.method, "sufficient": False, "reason": "not_fitted",
            "n_oof_races": 0, "n_oof_rows": 0, "n_positives": 0,
            "n_distinct_scores": 0, "n_dead_heat_races": 0, "n_incomplete_races": 0,
            "score_min": None, "score_max": None,
        }

    #: Every ModelRecipe field must be accounted for below, so that a field added to the recipe
    #: LATER cannot be silently ignored here. That is not hypothetical: `weight_mask_rate`
    #: (feature 091) was added after this builder was written and was dropped for a whole
    #: release — producing a booster that trained fine, scored fine on the full-information
    #: window, and was blind to the path serving takes. A hand-picked subset of fields is the
    #: defect; this map is the fail-closed guard against the next instance of it.
    #:
    #:   "forward" = passed straight through to the booster
    #:   "override" = arm E deliberately differs (this IS the arm: full-history booster, no
    #:                calibration holdout, calibration fitted out-of-fold instead)
    #:   "not-applicable" = carries no meaning for the booster itself
    _RECIPE_FIELD_DISPOSITION = {
        "objective": "forward",
        "target_encode_cols": "forward",
        "te_smoothing": "forward",
        "seed": "forward",
        "drop_features": "forward",
        "params": "forward",             # via resolved_params(); 容量はモデル同一性の一部
        "weight_mask_rate": "forward",   # via weight_mask_spec()
        "weight_mask_seed": "forward",   # via weight_mask_spec()
        "calibration": "override",       # -> "none"; the OOF calibrator is grafted on after
        "calib_frac": "override",        # -> 0.0; full-history booster is the point of the arm
        "calibration_split_unit": "override",  # no contiguous holdout exists to split
        "market_offset": "not-applicable",     # arm E is defined on the non-offset recipe
        "ev_weight": "not-applicable",         # kill-test-only, never active
        "label": "not-applicable",             # provenance string
    }

    def _check_recipe_fields_accounted_for(self) -> None:
        known = set(self._RECIPE_FIELD_DISPOSITION)
        actual = {f.name for f in dataclasses.fields(self.recipe)}
        if unhandled := actual - known:
            raise ArmNotServable(
                f"ModelRecipe gained field(s) {sorted(unhandled)} that arm E's booster builder "
                "does not account for. Add them to _RECIPE_FIELD_DISPOSITION — forwarding them "
                "if they change the booster, marking them override/not-applicable otherwise. "
                "Failing closed here beats registering a model that quietly ignores the recipe."
            )

    def _make_base(self) -> LightGBMPredictor:
        self._check_recipe_fields_accounted_for()
        p = LightGBMPredictor(
            self.session,
            objective=self.recipe.objective,
            calibration="none",  # booster gives race-normalized p; OOF power is applied on top
            # FULL-history booster: no train-internal calibration holdout is carved off (that is
            # the whole point of arm C/D). Without this, LightGBMPredictor.fit silently ran its
            # default 70/30 split and the booster never learned the latest 30% — the bug the
            # 2026-07 training-logic review found (the 068 C/D verdicts predate this fix).
            calib_frac=0.0,
            target_encode_cols=self.recipe.target_encode_cols,
            te_smoothing=self.recipe.te_smoothing,
            seed=self.recipe.seed,
            drop_features=self.recipe.drop_features,
            # Feature 091: the fit-scope weight mask. Arm E overrides the CALIBRATION fields
            # above — that is what the arm is — but everything else must survive, and dropping
            # this one is invisible: the booster still trains, still scores well on the
            # full-information window, and is simply blind to the path serving actually takes
            # (97% of live races have no same-day weight). Omitting it here recorded a
            # pre-091 model as "arm E of this recipe".
            fit_weight_mask=self.recipe.weight_mask_spec(),
            # 容量。arm E は booster が見る行が約 40% 増えるので、旧レシピで測った最適本数が
            # そのまま当てはまる保証は無い。ここを配線しないと arm E で容量を振れない。
            params=self.recipe.resolved_params(),
        )
        if self._shared is not None:
            p._data = self._shared
        return p

    def fit(self, train_races: list[RaceContext], *, num_threads: int | None = None):
        # reset before refit: a reused predictor must not carry a previous fold's calibrator
        # when this fold yields no OOF samples (identity is the correct fallback).
        self._reset_calibration_state()
        self._base = self._make_base()
        self._base.fit(train_races)
        if self.method == "isotonic":
            self._fit_oof_isotonic(train_races)
        else:
            samples = self._oof_samples(train_races)
            if samples:
                self.gamma_, self.n_oof_samples_ = fit_power_gamma(samples)
                self.oof_info_.update(
                    sufficient=True, reason="ok", n_oof_races=self.n_oof_samples_
                )
            else:
                self.oof_info_.update(reason="no_oof_samples")
                if self.require_sufficient:
                    raise InsufficientOofSample(
                        "OOF power calibration produced no samples (fail-closed; pass "
                        "require_sufficient=False to allow the identity fallback)"
                    )
        return self

    # --- arm E: strict-past OOF isotonic -------------------------------------
    def _fit_oof_isotonic(self, train_races: list[RaceContext]) -> None:
        """Feature 085 arm E: fit ONE isotonic map per outer fold on strict-past OOF rows.

        Rows are ``(raw race-softmax score, started-all win label)`` per started horse, produced
        by inner boosters trained only on strictly-earlier race-days (§3.1/§3.2). Insufficient
        folds fall back to identity with a machine-readable reason (§3.3).
        """
        scores, labels, info = self._oof_isotonic_rows(train_races)
        self.oof_info_.update(info)
        distinct = int(np.unique(scores).size) if len(scores) else 0
        self.oof_info_["n_distinct_scores"] = distinct
        if len(scores):
            self.oof_info_["score_min"] = float(np.min(scores))
            self.oof_info_["score_max"] = float(np.max(scores))
        n_pos = int(np.sum(labels == 1)) if len(labels) else 0
        self.oof_info_["n_positives"] = n_pos

        reason = None
        if info["n_oof_races"] < MIN_OOF_RACES:
            reason = f"too_few_oof_races({info['n_oof_races']}<{MIN_OOF_RACES})"
        elif len(scores) < MIN_OOF_ROWS:
            reason = f"too_few_oof_rows({len(scores)}<{MIN_OOF_ROWS})"
        elif n_pos < MIN_OOF_POSITIVES:
            reason = f"too_few_positives({n_pos}<{MIN_OOF_POSITIVES})"
        elif distinct < MIN_OOF_DISTINCT_SCORES:
            reason = f"too_few_distinct_scores({distinct}<{MIN_OOF_DISTINCT_SCORES})"
        elif int(np.unique(labels).size) < 2:
            reason = "single_class_labels"
        if reason is not None:
            self.oof_info_.update(sufficient=False, reason=reason)
            if self.require_sufficient:
                raise InsufficientOofSample(
                    f"OOF isotonic sample insufficient: {reason} (fail-closed; pass "
                    "require_sufficient=False to allow the identity fallback)"
                )
            return

        # same wrapper/settings A uses, so the fitted object is servable as-is (085 §5)
        self.calibrator_ = fit_calibrator(
            scores, labels, method="isotonic", clip=DEFAULT_CLIP
        )
        self.n_oof_samples_ = len(scores)
        self.oof_info_.update(
            sufficient=True, reason="ok", n_oof_rows=len(scores),
            calibrator_degenerate=self.calibrator_.identity,
        )

    def _oof_isotonic_rows(self, train_races: list[RaceContext]):
        """(scores, labels, info) over every eligible OOF race of this outer fold."""
        races = sorted(train_races, key=lambda r: (r.race_date, r.race_id))
        days = sorted({r.race_date for r in races})
        # oof_pred_from/through record the race-day span the OOF PREDICTIONS actually cover —
        # the blocks, never the earlier days used to fit them. Without it the shipped provenance
        # cannot answer "which races produced the calibration sample", which 085 §5 requires.
        info: dict = {
            "n_oof_races": 0, "n_oof_rows": 0, "n_dead_heat_races": 0, "n_incomplete_races": 0,
            "oof_pred_from": None, "oof_pred_through": None,
        }
        if len(days) < self.n_oof * 2:
            return np.empty(0), np.empty(0), info
        outcomes = _started_all_outcomes(self.session, [r.race_id for r in races])
        scores: list[float] = []
        labels: list[int] = []
        for earlier_days, block_days in day_block_partition(days, self.n_oof):
            eset, bset = set(earlier_days), set(block_days)
            earlier = [r for r in races if r.race_date in eset]
            block = [r for r in races if r.race_date in bset]
            if not earlier or not block:
                continue
            pred = self._make_base()
            pred.fit(earlier)
            for ctx in block:
                got = outcomes.get(ctx.race_id)
                if got is None:
                    info["n_incomplete_races"] += 1  # no result rows at all
                    continue
                n_result_rows, winners = got
                started = [h.horse_id for h in ctx.started_horses]
                if not started or n_result_rows < len(started):
                    # partial ingest: "absent result = 0" is unverifiable (eval fail-closed rule)
                    info["n_incomplete_races"] += 1
                    continue
                if not winners:
                    info["n_incomplete_races"] += 1  # no finished 1st place -> no positive
                    continue
                if len(winners) > 1:
                    info["n_dead_heat_races"] += 1  # kept: every 1st-place horse is a positive
                ids, raw = pred.raw_win_probs(ctx)  # UNCALIBRATED race-softmax (§3.1)
                if list(ids) != started or not np.isfinite(raw).all():
                    raise RuntimeError(
                        f"arm E: prediction/started mismatch or non-finite score for {ctx.race_id}"
                    )
                info["n_oof_races"] += 1
                day = ctx.race_date.isoformat()
                if info["oof_pred_from"] is None or day < info["oof_pred_from"]:
                    info["oof_pred_from"] = day
                if info["oof_pred_through"] is None or day > info["oof_pred_through"]:
                    info["oof_pred_through"] = day
                for hid, s in zip(ids, raw, strict=True):
                    scores.append(float(s))
                    labels.append(1 if hid in winners else 0)
        info["n_oof_rows"] = len(scores)
        return np.asarray(scores, dtype=float), np.asarray(labels, dtype=int), info

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

    def to_servable(self) -> LightGBMPredictor:
        """Return the inner booster with the OOF isotonic grafted on — the shippable object (§5).

        No new artifact format is needed. ``_base`` is an ordinary LightGBMPredictor and the
        isotonic was fitted on the RAW race-softmax, which is exactly the vector serving hands to
        ``calibrator.transform`` (serving/predictor.py: raw_predict -> calibrator.transform). Had
        the fit been on ``predict_race`` output (clipped + renormalised, as arm C/D does) this
        graft would be silently wrong, which is why §3.1 froze the score space.

        ``fit_info_`` is REWRITTEN to tell the truth. ``_make_base`` builds the booster with
        ``calibration="none"`` (correct — the booster carries no calibrator), so shipping it
        unchanged would record "this model has no calibration" while a fitted isotonic sits in
        calibrator.pkl next to it. Nothing in serving reads that field, but the registry and any
        human auditor do.
        """
        if self.method != "isotonic":
            raise ArmNotServable(f"only arm E (isotonic) is servable, not method={self.method!r}")
        if self._base is None or self.calibrator_ is None:
            raise ArmNotServable("fit() must run before to_servable()")
        # Fail closed on the identity fallback. An insufficient OOF sample leaves an identity
        # calibrator; shipping THAT under the name strict_past_oof_isotonic_v1 would register an
        # uncalibrated model as a calibrated one.
        if not self.oof_info_.get("sufficient"):
            raise ArmNotServable(
                "refusing to ship arm E with an unfitted calibrator: "
                f"oof reason={self.oof_info_.get('reason')!r} (fail-closed)"
            )
        if self.calibrator_.identity:
            raise ArmNotServable("refusing to ship a degenerate (identity) OOF isotonic")

        base = self._base
        base.calibrator_ = self.calibrator_
        info = dict(base.fit_info_ or {})
        info["calibration"] = "isotonic_strict_past_oof"
        # arm E has NO contiguous calibration holdout, so the window/split vocabulary does not
        # apply. Writing a value here would make it look like a 70/30 split model.
        info["calibration_split_unit"] = None
        info["calib_from"] = None
        info["calib_through"] = None
        info["n_calib_rows"] = int(self.n_oof_samples_)
        info["calibration_protocol"] = {
            "protocol": "strict_past_oof_isotonic_v1",
            "booster_calib_frac": 0.0,
            "n_oof_blocks": self.n_oof,
            "n_oof_rows": self.oof_info_.get("n_oof_rows"),
            "n_oof_races": self.oof_info_.get("n_oof_races"),
            "n_positives": self.oof_info_.get("n_positives"),
            "n_distinct_scores": self.oof_info_.get("n_distinct_scores"),
            "oof_pred_from": self.oof_info_.get("oof_pred_from"),
            "oof_pred_through": self.oof_info_.get("oof_pred_through"),
            "score_space": "raw_race_softmax",
            "threshold_checksum": self._threshold_checksum(),
            "n_calib_rows_note": (
                "counts the OOF rows the isotonic was fitted on; the booster held out NOTHING "
                "(booster_calib_frac=0.0), so this is not a holdout size"
            ),
        }
        base.fit_info_ = info
        return base

    def _threshold_checksum(self) -> str:
        """Stable digest of the fitted isotonic so a swapped calibrator is detectable."""
        params = self.calibrator_.params_dict() if self.calibrator_ else None
        payload = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def predict_race(self, race: RaceContext) -> dict:
        assert self._base is not None
        if self.method == "isotonic":
            # arm E: isotonic on the RAW race-softmax (the vector serving's calibrator receives),
            # then clip + race-renormalize + Harville via assemble_predictions = A's semantics.
            started_ids, raw = self._base.raw_win_probs(race)
            win_scores = (
                np.asarray(self.calibrator_.transform(raw), dtype=float)
                if self.calibrator_ is not None else np.asarray(raw, dtype=float)
            )
            return assemble_predictions(started_ids, win_scores)
        base = self._base.predict_race(race)
        p = {hid: v.win for hid, v in base.items()}
        pcal = _apply_gamma(p, self.gamma_)  # race-normalized power, Σ=1 preserved (IV)
        started_ids = [h.horse_id for h in race.started_horses]
        win_scores = np.asarray([pcal[hid] for hid in started_ids], dtype=float)
        return assemble_predictions(started_ids, win_scores)


@dataclass
class CalibSplitFactory:
    """eval PredictorFactory for the OOF-calibrated arms (recipe-refit per outer fold).

    ``method="power"`` = 068 arm C/D; ``method="isotonic"`` = 085 arm E. The method enters
    ``recipe_meta`` (hence ``recipe_hash``), so the two arms are distinct model identities and a
    report can never be mistaken for the other arm.
    """

    session: Session
    recipe: ModelRecipe
    n_oof_blocks: int = 3
    method: str = "power"
    require_sufficient: bool = True
    _pred: OofCalibratedPredictor | None = field(default=None, init=False, repr=False)
    _shared: TrainingMatrix | None = field(default=None, init=False, repr=False)

    @property
    def recipe_meta(self) -> dict:
        return {
            **self.recipe.meta(), "arm": f"oof_{self.method}",
            "n_oof_blocks": self.n_oof_blocks,
        }

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
                method=self.method, require_sufficient=self.require_sufficient,
            )
        self._pred.fit(train_races)
        return self._pred
