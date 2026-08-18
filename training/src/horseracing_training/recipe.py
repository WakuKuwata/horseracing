"""ModelRecipe + PredictorFactory (Feature 068, T003, data-model §2/§2b).

A ``ModelRecipe`` is the *processing* description of an arm — enough to RE-FIT a predictor on
each outer fold's train rows (codex C1: the saved booster is a full-history serving model, so
applying it to past races is in-sample). ``RecipeFactory`` implements the eval-side
``PredictorFactory`` protocol structurally, so ``eval`` drives per-fold refits without importing
``training`` (020 boundary; the dependency edge is training→eval, which is allowed).

``market_offset`` must be False (FR-019, codex C3): a true value makes the predictor read the
target race's own odds, a leak. Construction fails closed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from horseracing_eval.hashing import stable_hash
from horseracing_eval.predictor import Predictor, RaceContext
from sqlalchemy.orm import Session

from .calibration import (
    CALIBRATION_SPLIT_UNITS,
    DEFAULT_CALIB_FRAC,
    LEGACY_CALIBRATION_SPLIT_UNIT,
)
from .predictor import LightGBMPredictor
from .target_encoding import DEFAULT_SMOOTHING


class MarketOffsetForbidden(ValueError):
    """Raised when a 068 recipe requests market_offset (leak vector, FR-019)."""


@dataclass(frozen=True)
class ModelRecipe:
    objective: str = "pl_topk"
    calibration: str = "isotonic"
    calib_frac: float = DEFAULT_CALIB_FRAC
    # Feature 073 (US2, FR-009): explicit calibration split unit. Default = legacy race-count
    # split so existing recipes stay byte-identical (see recipe_hash back-compat below, D1).
    calibration_split_unit: str = LEGACY_CALIBRATION_SPLIT_UNIT
    target_encode_cols: tuple[str, ...] = ("jockey_id", "trainer_id")
    te_smoothing: float = DEFAULT_SMOOTHING
    seed: int = 42
    drop_features: tuple[str, ...] = ()
    market_offset: bool = False
    #: Feature 079: EV-weighted training — the model-fit rows carry a per-RACE scalar weight
    #: (α_r from OOF-EV, see ev_weight.py). This makes the model explicitly MARKET-AWARE (the
    #: weight reads odds), so it is a retrospective, artifact-only kill-test — never active/default.
    #: The frozen OOF-p source is supplied to RecipeFactory (fit-scope), not hashed here.
    #: Default False is OMITTED from recipe_hash (back-compat), so every pre-079 recipe is
    #: byte-identical; True yields a distinct recipe_hash/model identity.
    ev_weight: bool = False
    #: Feature 091: race-atomic masking of the same-day weight columns during the fit (and the
    #: calibration holdout, D4). This is the MECHANISM, not a tweak — without it `prev_weight` is
    #: shadowed by the near-identical `weight` column and the model never learns the serving path.
    #: `None` (default) is OMITTED from recipe_hash, so every pre-091 recipe hashes identically.
    #: NOTE `weight_mask_rate=0.0` is NOT the same as `None`: 0.0 records "masking was deliberately
    #: switched off for this experiment", None records "this recipe predates the mechanism".
    weight_mask_rate: float | None = None
    weight_mask_seed: int | None = None
    #: LightGBM の容量など、既定からの上書きだけを (key, value) の組で持つ。dict ではなく
    #: タプルなのは frozen dataclass を本当に不変にするため。`None`(上書きなし)は
    #: recipe_hash から省かれるので、**これ以前のレシピは 1 件もハッシュが動かない**。
    #: 容量はモデルの同一性そのものなので fit-scope ではなく recipe に置く: 900 本の木の
    #: モデルは 300 本のモデルと別物であり、別の model_version であるべきである。
    params: tuple[tuple[str, float | int], ...] | None = None
    label: str = ""

    def __post_init__(self) -> None:
        # FR-019 / codex C3: fail closed — 068 never reads the target race's own odds.
        if self.market_offset is not False:
            raise MarketOffsetForbidden(
                "068 recipes must set market_offset=False (reading the target race's own "
                "odds is a leak, FR-019)"
            )
        # Feature 091: rate and seed travel together — a rate without a seed is not reproducible.
        if (self.weight_mask_rate is None) != (self.weight_mask_seed is None):
            raise ValueError(
                "weight_mask_rate and weight_mask_seed must both be set or both be None "
                f"(got rate={self.weight_mask_rate!r}, seed={self.weight_mask_seed!r})"
            )
        if self.weight_mask_rate is not None and not 0.0 <= self.weight_mask_rate <= 1.0:
            raise ValueError(f"weight_mask_rate must be in [0, 1] (got {self.weight_mask_rate!r})")
        # Feature 073 FR-009/FR-002: fail closed on an unknown split unit.
        if self.calibration_split_unit not in CALIBRATION_SPLIT_UNITS:
            raise ValueError(
                f"unknown calibration_split_unit: {self.calibration_split_unit!r} "
                f"(expected one of {CALIBRATION_SPLIT_UNITS})"
            )

    def meta(self) -> dict:
        """Plain-dict audit view (no training types cross the eval boundary, analyze C1).

        The full view (including ``calibration_split_unit``) is what audit artifacts record.
        """
        return asdict(self)

    def recipe_hash(self) -> str:
        """Content hash with Feature 073 back-compat canonicalization (D1).

        The legacy default split unit (``race_count_v1``) is OMITTED from the hashed dict so
        every recipe authored before 073 keeps a byte-identical ``recipe_hash`` (SC-006). Only a
        non-legacy split (``race_day_v1``) enters the hash — changing the split therefore forces
        a new ``recipe_hash`` and ``model_version``. Serving prediction bytes are artifact-derived
        and independent of ``recipe_hash``, so SC-005 holds regardless of this field.
        """
        d = self.meta()
        if d.get("calibration_split_unit") == LEGACY_CALIBRATION_SPLIT_UNIT:
            d = {k: v for k, v in d.items() if k != "calibration_split_unit"}
        # Feature 079: default (off) EV-weighting is omitted so pre-079 recipes hash identically.
        if d.get("ev_weight") is False:
            d = {k: v for k, v in d.items() if k != "ev_weight"}
        # Feature 091: an ABSENT weight mask (None) is omitted so pre-091 recipes hash identically.
        # An EXPLICIT rate — including 0.0 — stays in the hash: "masking deliberately off" is a
        # different model identity from "recipe predates masking" (provenance, not just value).
        if d.get("weight_mask_rate") is None:
            d = {k: v for k, v in d.items() if k not in ("weight_mask_rate", "weight_mask_seed")}
        # 容量の上書きが無い(None)レシピは、この欄が足される前と同じハッシュになる。
        if d.get("params") is None:
            d = {k: v for k, v in d.items() if k != "params"}
        return stable_hash(d)

    def resolved_params(self) -> dict | None:
        """LightGBM に渡す完全な params。上書きが無ければ None(= 既定のまま・挙動不変)。"""
        if not self.params:
            return None
        from .win_model import DEFAULT_PARAMS

        return {**DEFAULT_PARAMS, **dict(self.params)}

    def weight_mask_spec(self):
        """The features-layer MaskSpec for this recipe, or None when masking is not configured."""
        if self.weight_mask_rate is None:
            return None
        from horseracing_features.weight_mask import MaskSpec

        return MaskSpec(rate=self.weight_mask_rate, seed=self.weight_mask_seed, unit="race")


@dataclass
class RecipeFactory:
    """eval ``PredictorFactory`` — refits one arm per fold, caching the feature matrix.

    A single ``LightGBMPredictor`` is created lazily (building the matrix once) and re-fit on
    each fold's train rows, mirroring the harness's single-predictor-per-arm pattern so the
    matrix build is not repeated per fold.
    """

    session: Session
    recipe: ModelRecipe
    #: Feature 074 (D9): restrict the fit to a legacy model's exact ordered columns (e.g. lgbm-063
    #: features-017 columns) so OOF regeneration on the current features-018 schema is recipe-
    #: faithful. None = use the recipe's full schema. NOT part of recipe_hash (fit-scope, not
    #: model identity) — the restriction is recorded via the legacy attestation the OOF bundle
    #: references.
    restrict_features: tuple[str, ...] | None = None
    #: Feature 079: frozen OOF win-prob source {(race_id, horse_id) -> p} for EV-weight
    #: construction, required iff recipe.ev_weight is True (fail-closed in the predictor). Not
    #: part of recipe_hash (fit-scope; referenced by digest in the evidence artifact). None when
    #: ev_weight is off. The SAME frozen bundle is used across all folds (strict-past guarantee
    #: is the bundle's, not the weighted fit's — 079 never iterates weights from itself).
    oof_p: dict | None = None
    #: Feature 091 (research D16): read the as-of block from a materialised parquet instead of the
    #: live database. Evaluation is the one caller that WANTS a frozen input — the database moves
    #: under a multi-hour run (measured: 4.8% of the eval window's rows were rewritten between two
    #: confirmatory runs), which is why the same arms produced −0.010592 one day and −0.010539 the
    #: next against a config declaring a 1e-9 tolerance. Not part of recipe_hash: this is fit
    #: SCOPE, like restrict_features, not model identity.
    use_materialized: bool = False
    materialized_path: str | None = None
    #: Deliberately read the parquet even though the database has moved past it. For SERVING that
    #: would be a bug (silently stale features); for a paired comparison it is the entire point —
    #: both arms must see the same bytes, and the run must be repeatable next week. Always
    #: recorded in the report so a pinned run is never mistaken for a fresh one.
    pin_snapshot: bool = False
    _pred: LightGBMPredictor | None = field(default=None, init=False, repr=False)

    @property
    def recipe_meta(self) -> dict:
        return self.recipe.meta()

    @property
    def recipe_hash(self) -> str:
        return self.recipe.recipe_hash()

    def fit(self, train_races: list[RaceContext], *, num_threads: int | None = None) -> Predictor:
        if self._pred is None:
            self._pred = LightGBMPredictor(
                self.session,
                seed=self.recipe.seed,
                calibration=self.recipe.calibration,
                calib_frac=self.recipe.calib_frac,
                target_encode_cols=self.recipe.target_encode_cols,
                te_smoothing=self.recipe.te_smoothing,
                drop_features=self.recipe.drop_features,
                objective=self.recipe.objective,
                params=self.recipe.resolved_params(),
                market_offset=self.recipe.market_offset,
                calibration_split_unit=self.recipe.calibration_split_unit,
                restrict_features=self.restrict_features,
                ev_weight=self.recipe.ev_weight,
                oof_p=self.oof_p,
                # Feature 091: the FIT-scope mask (training mixture). The PREDICT-scope regime is
                # set separately per regime by eval via set_predict_weight_mask — one fit, many
                # regimes.
                fit_weight_mask=self.recipe.weight_mask_spec(),
                use_materialized=self.use_materialized,
                materialized_path=self.materialized_path,
                skip_fingerprint_verify=self.pin_snapshot,
            )
        self._pred.fit(train_races)
        return self._pred
