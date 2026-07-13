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

from .calibration import DEFAULT_CALIB_FRAC
from .predictor import LightGBMPredictor
from .target_encoding import DEFAULT_SMOOTHING


class MarketOffsetForbidden(ValueError):
    """Raised when a 068 recipe requests market_offset (leak vector, FR-019)."""


@dataclass(frozen=True)
class ModelRecipe:
    objective: str = "pl_topk"
    calibration: str = "isotonic"
    calib_frac: float = DEFAULT_CALIB_FRAC
    target_encode_cols: tuple[str, ...] = ("jockey_id", "trainer_id")
    te_smoothing: float = DEFAULT_SMOOTHING
    seed: int = 42
    drop_features: tuple[str, ...] = ()
    market_offset: bool = False
    label: str = ""

    def __post_init__(self) -> None:
        # FR-019 / codex C3: fail closed — 068 never reads the target race's own odds.
        if self.market_offset is not False:
            raise MarketOffsetForbidden(
                "068 recipes must set market_offset=False (reading the target race's own "
                "odds is a leak, FR-019)"
            )

    def meta(self) -> dict:
        """Plain-dict audit view (no training types cross the eval boundary, analyze C1)."""
        return asdict(self)

    def recipe_hash(self) -> str:
        return stable_hash(self.meta())


@dataclass
class RecipeFactory:
    """eval ``PredictorFactory`` — refits one arm per fold, caching the feature matrix.

    A single ``LightGBMPredictor`` is created lazily (building the matrix once) and re-fit on
    each fold's train rows, mirroring the harness's single-predictor-per-arm pattern so the
    matrix build is not repeated per fold.
    """

    session: Session
    recipe: ModelRecipe
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
                market_offset=self.recipe.market_offset,
            )
        self._pred.fit(train_races)
        return self._pred
